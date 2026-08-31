"""Crawl orchestration: workers, rendering, checkpointing, and the abort guard."""
from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import asdict
from pathlib import Path

import httpx

from .fetcher import Fetcher, build_client
from .intake import resolve_host
from .parse import links_for_frontier, parse_into
from .sitemaps import collect_sitemaps
from .template import representative_urls, signature as template_signature
from .frontier import Frontier
from .htmlstore import HtmlStore
from .models import CrawlConfig, PageRecord
from .politeness import RateLimiter, RobotsGate
from .urlnorm import normalise

# If more than this share of responses are 403/429, the site is refusing us and
# any audit we produce would be nonsense. Stop and say so.
BLOCK_ABORT_RATIO = 0.20
BLOCK_ABORT_MIN_SAMPLE = 15

# Images are sized after the crawl, in one batch. Capped because a large site
# can reference thousands and check PRF-04 only needs the big ones found.
MAX_IMAGE_HEADS = 400


class CrawlBlocked(RuntimeError):
    """Raised when the host is refusing the crawler."""


class Crawler:
    def __init__(self, config: CrawlConfig, out_dir: Path, *, render: bool = True) -> None:
        self.config = config
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.render_enabled = render

        root = normalise(config.domain) or config.domain
        self.root = root
        self.frontier = Frontier(root, max_depth=config.max_depth, max_pages=config.max_pages)
        self.limiter = RateLimiter(config.requests_per_second)
        self.robots = RobotsGate(config.user_agent, obey=config.obey_robots)
        self.fetcher = Fetcher(config, self.limiter, self.robots)
        self.store = HtmlStore(out_dir / "html")
        self.renderer = None

        self.records: list[PageRecord] = []
        self.host: object | None = None
        self.sitemap = None
        self.interrupted = False
        # Records stream to disk as they are fetched. A crawl that dies at
        # page 450 — a protocol error, a Ctrl-C, a closed laptop — must keep
        # the 450 pages it politely fetched, not discard them.
        self._pages_fh = (self.out_dir / "pages.jsonl").open("w", encoding="utf-8")
        self._blocked_responses = 0
        self._total_responses = 0
        self._render_failures = 0
        self._lock = asyncio.Lock()

    async def run(self, on_progress=None) -> list[PageRecord]:
        async with AsyncExitStack() as stack:
            client = await stack.enter_async_context(build_client(self.config))

            # Resolve the real host before anything else. Crawling the wrong
            # variant makes every internal link look like a cross-host redirect.
            self.host = await resolve_host(client, self.root)
            if self.host.canonical != self.root:
                self.root = self.host.canonical
                self.frontier.root = self.root

            self.frontier.add(self.root, depth=0, source="seed")

            if self.render_enabled:
                from .renderer import Renderer  # imported lazily: playwright is heavy
                self.renderer = await stack.enter_async_context(
                    Renderer(
                        self.config.user_agent,
                        concurrency=self.config.concurrency,
                        timeout_s=self.config.render_timeout_s,
                        settle_ms=self.config.render_settle_ms,
                    )
                )

            await self.robots.load(client, self.root)

            delay = self.robots.crawl_delay(self.root)
            if delay:
                self.limiter.set_min_interval(delay)
            # Sitemaps: seed the frontier from them so that anything listed
            # but unlinked is still crawled — and recorded as sitemap-only,
            # which is what makes the orphan check possible.
            self.sitemap = await collect_sitemaps(
                client, self.root, self.robots.sitemaps(self.root)
            )
            for url in self.sitemap.urls:
                self.frontier.add(url, depth=1, source="sitemap")

            workers = [
                asyncio.create_task(self._worker(client, on_progress))
                for _ in range(self.config.concurrency)
            ]
            try:
                await asyncio.gather(*workers)
            except (KeyboardInterrupt, asyncio.CancelledError):
                self.interrupted = True
            finally:
                for w in workers:
                    w.cancel()

            if self.config.size_images and not self.interrupted:
                try:
                    await self._size_images(client)
                except Exception:
                    pass          # sizing is a bonus, never a reason to fail

        self._write_outputs()
        return self.records

    async def _worker(self, client: httpx.AsyncClient, on_progress) -> None:
        while True:
            async with self._lock:
                item = self.frontier.pop()
            if item is None:
                return

            try:
                await self._process(item, client)
            except CrawlBlocked:
                raise
            except Exception as exc:
                # A page that blows up in parsing, rendering or storage is one
                # lost page, recorded as such — not a dead crawl.
                async with self._lock:
                    failed = PageRecord(url=item.url, depth=item.depth)
                    failed.fetch_error = f"{type(exc).__name__}: {exc}"
                    self._append(failed)

            if on_progress:
                on_progress(len(self.records), self.frontier.stats())

    async def _process(self, item, client: httpx.AsyncClient) -> None:
        record, links, body = await self.fetcher.fetch(
            client, item.url, depth=item.depth,
            sources=self.frontier.sources_for(item.url),
        )

        if body:
            self.store.put("raw", record.url, body)

        # Render only real HTML pages we actually got. No point loading a
        # 404 or a redirect target in a browser.
        rendered_html = ""
        if self.renderer and record.ok and record.is_html:
            rendered_html, links = await self._render(record, links)

        # Parse the DOM a visitor actually sees. Rendered when we have it,
        # raw otherwise — parsing raw HTML on a JS site would report an
        # empty page that is not empty.
        if record.is_html:
            page_html = rendered_html or body.decode("utf-8", "replace")
            parse_into(record, page_html, self.root)
            record.template_signature = template_signature(page_html)

        async with self._lock:
            self._append(record)
            if record.status is not None:
                self._total_responses += 1
                if record.status in (403, 429):
                    self._blocked_responses += 1
            self._check_abort()

            base = record.final_url or record.url
            for href in links:
                self.frontier.add(href, depth=item.depth + 1,
                                  source="internal_link", base=base)
            record.discovered_via = self.frontier.sources_for(record.url)

            if len(self.records) % 10 == 0:
                self._checkpoint()

    def _append(self, record: PageRecord) -> None:
        """Keep the record in memory and on disk in the same breath."""
        self.records.append(record)
        self._pages_fh.write(json.dumps(asdict(record), default=str) + "\n")
        self._pages_fh.flush()

    async def _render(self, record: PageRecord,
                      raw_links: list[str]) -> tuple[str, list[str]]:
        result = await self.renderer.render(record.final_url or record.url)

        if not result.ok and not result.html:
            record.render_timed_out = result.timed_out
            async with self._lock:
                self._render_failures += 1
            # Rendering failed; the raw links are still perfectly good.
            return "", raw_links

        record.rendered_html_len = len(result.html.encode("utf-8", "replace"))
        record.render_timed_out = result.timed_out
        self.store.put("rendered", record.url, result.html)

        # Links from the rendered DOM, unioned with the raw ones. A nav built
        # by JavaScript only exists here — this line is why Wix sites crawl at
        # all. Order-preserving dedupe keeps breadth-first behaviour sane.
        rendered_links = links_for_frontier(result.html, record.final_url or record.url)
        seen, merged = set(), []
        for href in [*raw_links, *rendered_links]:
            if href not in seen:
                seen.add(href)
                merged.append(href)
        return result.html, merged

    def _check_abort(self) -> None:
        if self._total_responses < BLOCK_ABORT_MIN_SAMPLE:
            return
        ratio = self._blocked_responses / self._total_responses
        if ratio >= BLOCK_ABORT_RATIO:
            raise CrawlBlocked(
                f"{self._blocked_responses}/{self._total_responses} responses were "
                f"403/429. The host is blocking the crawler — ask the client to "
                f"allowlist the CrawlspaceBot user-agent before re-running."
            )

    async def _size_images(self, client: httpx.AsyncClient) -> None:
        """Fill ImageRef.bytes with a HEAD request per unique image.

        Done once at the end rather than during parsing: the same hero image
        appears on every page, and sizing it 300 times would be both slow and
        rude. One request per distinct URL, results shared back to every record
        that references it.
        """
        unique: dict[str, list] = {}
        for record in self.records:
            for image in record.images:
                unique.setdefault(image.src, []).append(image)

        for src in list(unique)[:MAX_IMAGE_HEADS]:
            await self.limiter.acquire()
            size = None
            try:
                response = await client.head(src, timeout=10.0)
                if response.status_code == 405:      # some CDNs refuse HEAD
                    response = await client.get(src, timeout=15.0)
                if response.status_code == 200:
                    length = response.headers.get("content-length")
                    size = int(length) if length and length.isdigit() else None
            except (httpx.HTTPError, ValueError):
                size = None
            if size is not None:
                for image in unique[src]:
                    image.bytes = size

    def image_stats(self) -> dict:
        sized = [i for r in self.records for i in r.images if i.bytes]
        oversized = [i for i in sized if i.bytes > 200_000]
        return {
            "unique_images": len({i.src for r in self.records for i in r.images}),
            "sized": len(sized),
            "over_200kb": len(oversized),
            "largest_bytes": max((i.bytes for i in sized), default=0),
        }

    def orphans(self) -> list[str]:
        crawled = {r.url for r in self.records if r.ok}
        return self.frontier.orphans(crawled)

    @property
    def crawl_complete(self) -> bool:
        return self.frontier.complete

    def templates(self) -> dict:
        sigs = [r.template_signature for r in self.records if r.template_signature]
        return {
            "distinct": len(set(sigs)),
            "pages_with_signature": len(sigs),
            "representatives": representative_urls(self.records),
        }

    def stats(self) -> dict:
        rendered = [r for r in self.records if r.rendered_html_len]
        gaps = [
            r for r in rendered
            if r.raw_html_len and r.rendered_html_len > r.raw_html_len * 3
        ]
        timeouts = sum(1 for r in self.records if r.render_timed_out)
        # A metric that is always true is worse than no metric. On Wix or any
        # platform holding a socket open for analytics, network-idle never
        # arrives and every page gets flagged — 410 of 410 on azayaorganic.com.
        # Report the rate so it reads as a property of the platform rather than
        # a per-page fault.
        return {
            "rendered": len(rendered),
            "render_failures": self._render_failures,
            "render_timeouts": timeouts,
            "never_idle_pct": round(100 * timeouts / len(rendered)) if rendered else 0,
            "js_dependent": len(gaps),
        }

    def _checkpoint(self) -> None:
        state = {
            "root": self.root,
            "stats": self.frontier.stats(),
            "render": self.stats(),
            "fetched": len(self.records),
        }
        (self.out_dir / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _write_outputs(self) -> None:
        try:
            self._pages_fh.close()
        except Exception:
            pass

        site = {
            "root": self.root,
            "interrupted": self.interrupted,
            "host": asdict(self.host) if self.host else None,
            "sitemap": asdict(self.sitemap) if self.sitemap else None,
            "crawl_complete": self.crawl_complete,
            "orphans": self.orphans(),
            "orphans_valid": self.crawl_complete,
            "images": self.image_stats(),
            "templates": self.templates(),
            "frontier": self.frontier.stats(),
            "trap_reasons": self.frontier.trap_reasons,
            "discovery": self.frontier.discovery_sources(),
        }
        (self.out_dir / "site.json").write_text(
            json.dumps(site, indent=2, default=str), encoding="utf-8")
        self._checkpoint()
