"""Crawl orchestration: workers, checkpointing, and the abort guard."""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import httpx

from .fetcher import Fetcher, build_client
from .frontier import Frontier
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
    def __init__(self, config: CrawlConfig, out_dir: Path) -> None:
        self.config = config
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)

        root = normalise(config.domain) or config.domain
        self.root = root
        self.frontier = Frontier(root, max_depth=config.max_depth, max_pages=config.max_pages)
        self.limiter = RateLimiter(config.requests_per_second)
        self.robots = RobotsGate(config.user_agent, obey=config.obey_robots)
        self.fetcher = Fetcher(config, self.limiter, self.robots)

        self.records: list[PageRecord] = []
        self._blocked_responses = 0
        self._total_responses = 0
        self._lock = asyncio.Lock()

    async def run(self, on_progress=None) -> list[PageRecord]:
        self.frontier.add(self.root, depth=0, source="seed")

        async with build_client(self.config) as client:
            await self.robots.load(client, self.root)

            delay = self.robots.crawl_delay(self.root)
            if delay:
                self.limiter.set_min_interval(delay)

            for sm in self.robots.sitemaps(self.root):
                # Day 4 parses these properly. Day 1 just records that they exist.
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

            record, links = await self.fetcher.fetch(
                client, item.url, depth=item.depth,
                sources=self.frontier.sources_for(item.url),
            )

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
        path = self.out_dir / "sitemaps.txt"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(sitemap_url + "\n")

    def _checkpoint(self) -> None:
        state = {
            "root": self.root,
            "stats": self.frontier.stats(),
            "fetched": len(self.records),
        }
        (self.out_dir / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _write_outputs(self) -> None:
        with (self.out_dir / "pages.jsonl").open("w", encoding="utf-8") as fh:
            for rec in self.records:
                fh.write(json.dumps(asdict(rec), default=str) + "\n")
        self._checkpoint()
