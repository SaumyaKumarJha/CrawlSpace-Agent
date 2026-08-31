"""Crawl orchestration: workers, rendering, checkpointing, and the abort guard."""
from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import asdict
from pathlib import Path

import httpx

from .fetcher import Fetcher, build_client
from .parse import links_for_frontier, parse_into
from .frontier import Frontier
from .htmlstore import HtmlStore
from .models import CrawlConfig, PageRecord
from .politeness import RateLimiter, RobotsGate
from .urlnorm import normalise

# If more than this share of responses are 403/429, the site is refusing us and
# any audit we produce would be nonsense. Stop and say so.
BLOCK_ABORT_RATIO = 0.20
BLOCK_ABORT_MIN_SAMPLE = 15


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
        self._blocked_responses = 0
        self._total_responses = 0
        self._render_failures = 0
        self._lock = asyncio.Lock()

    async def run(self, on_progress=None) -> list[PageRecord]:
        self.frontier.add(self.root, depth=0, source="seed")

        async with AsyncExitStack() as stack:
            client = await stack.enter_async_context(build_client(self.config))

            if self.render_enabled:
                from .renderer import Renderer  # imported lazily: playwright is heavy
                self.renderer = await stack.enter_async_context(
                    Renderer(
                        self.config.user_agent,
                        concurrency=self.config.concurrency,
                        timeout_s=self.config.render_timeout_s,
                    )
                )

            await self.robots.load(client, self.root)

            delay = self.robots.crawl_delay(self.root)
            if delay:
                self.limiter.set_min_interval(delay)
            for sm in self.robots.sitemaps(self.root):
                self._note_sitemap(sm)

            workers = [
                asyncio.create_task(self._worker(client, on_progress))
                for _ in range(self.config.concurrency)
            ]
            try:
                await asyncio.gather(*workers)
            finally:
                for w in workers:
                    w.cancel()

        self._write_outputs()
        return self.records

    async def _worker(self, client: httpx.AsyncClient, on_progress) -> None:
        while True:
            async with self._lock:
                item = self.frontier.pop()
            if item is None:
                return

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
                parse_into(record, rendered_html or body.decode("utf-8", "replace"),
                           self.root)

            async with self._lock:
                self.records.append(record)
                if record.status is not None:
                    self._total_responses += 1
                    if record.status in (403, 429):
                        self._blocked_responses += 1
                self._check_abort()

                base = record.final_url or record.url
                for href in links:
                    self.frontier.add(href, depth=item.depth + 1,
                                      source="internal_link", base=base)

                if len(self.records) % 10 == 0:
                    self._checkpoint()

            if on_progress:
                on_progress(len(self.records), self.frontier.stats())

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

    def _note_sitemap(self, sitemap_url: str) -> None:
        with (self.out_dir / "sitemaps.txt").open("a", encoding="utf-8") as fh:
            fh.write(sitemap_url + "\n")

    def stats(self) -> dict:
        rendered = [r for r in self.records if r.rendered_html_len]
        gaps = [
            r for r in rendered
            if r.raw_html_len and r.rendered_html_len > r.raw_html_len * 3
        ]
        return {
            "rendered": len(rendered),
            "render_failures": self._render_failures,
            "render_timeouts": sum(1 for r in self.records if r.render_timed_out),
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
        with (self.out_dir / "pages.jsonl").open("w", encoding="utf-8") as fh:
            for rec in self.records:
                fh.write(json.dumps(asdict(rec), default=str) + "\n")
        self._checkpoint()
