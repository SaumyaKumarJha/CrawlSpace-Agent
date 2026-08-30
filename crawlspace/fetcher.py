"""HTTP fetch layer: one URL in, one PageRecord out.

Day 1 deliberately stops at the raw response. Rendering is day 2, parsing is
day 3. This module never looks inside the HTML except to pull links, so that
the fetch layer can be tested without a parser existing.
"""
from __future__ import annotations

import re
import time

import httpx

from .models import CrawlConfig, PageRecord
from .politeness import RateLimiter, RobotsGate

# Cheap link extraction so the frontier can be fed on day 1. parse.py replaces
# this properly on day 3 — this regex is a scaffold, not the real thing.
_HREF_RE = re.compile(rb"""<a\b[^>]*?\bhref\s*=\s*["']([^"'>]+)["']""", re.I)
_BASE_RE = re.compile(rb"""<base\b[^>]*?\bhref\s*=\s*["']([^"'>]+)["']""", re.I)

RETRY_STATUS = {429, 500, 502, 503, 504}


class Fetcher:
    def __init__(self, config: CrawlConfig, limiter: RateLimiter, robots: RobotsGate) -> None:
        self.config = config
        self.limiter = limiter
        self.robots = robots

    async def fetch(self, client: httpx.AsyncClient, url: str, *, depth: int,
                    sources: list[str]) -> tuple[PageRecord, list[str]]:
        record = PageRecord(url=url, depth=depth, discovered_via=sources)

        if not self.robots.allowed(url):
            record.fetch_error = "blocked_by_robots"
            return record, []

        body = b""
        started = time.perf_counter()
        response: httpx.Response | None = None

        for attempt in range(3):
            await self.limiter.acquire()
            try:
                response = await client.get(url, follow_redirects=True)
            except httpx.HTTPError as exc:
                record.fetch_error = f"{type(exc).__name__}: {exc}"
                if attempt == 2:
                    return record, []
                continue

            if response.status_code in RETRY_STATUS and attempt < 2:
                # Respect Retry-After when the server bothers to send one.
                retry_after = response.headers.get("retry-after")
                if retry_after and retry_after.isdigit():
                    self.limiter.set_min_interval(min(float(retry_after), 10.0))
                continue
            break

        if response is None:
            return record, []

        record.fetch_error = None
        record.response_ms = int((time.perf_counter() - started) * 1000)
        record.status = response.status_code
        record.final_url = str(response.url)
        record.redirect_chain = [str(r.url) for r in response.history]
        record.content_type = response.headers.get("content-type", "")
        record.headers = {
            k.lower(): v for k, v in response.headers.items()
            if k.lower() in {
                "content-type", "content-encoding", "cache-control", "x-robots-tag",
                "content-length", "server", "vary", "strict-transport-security",
                "last-modified", "etag",
            }
        }

        if not record.is_html:
            return record, []

        body = response.content
        record.raw_html_len = len(body)
        return record, self._extract_links(body, record.final_url or url)

    @staticmethod
    def _extract_links(body: bytes, page_url: str) -> list[str]:
        base_match = _BASE_RE.search(body)
        base = page_url
        if base_match:
            try:
                base = base_match.group(1).decode("utf-8", "ignore") or page_url
            except Exception:
                base = page_url
        out: list[str] = []
        for m in _HREF_RE.finditer(body):
            try:
                href = m.group(1).decode("utf-8", "ignore")
            except Exception:
                continue
            if href:
                out.append(href)
        return out


def _http2_available() -> bool:
    try:
        import h2  # noqa: F401
    except ImportError:
        return False
    return True


def build_client(config: CrawlConfig) -> httpx.AsyncClient:
    # HTTP/2 is a nice-to-have. Some client sites need it, none require it, and
    # a missing optional dependency should not stop a crawl.
    return httpx.AsyncClient(
        headers={
            "User-Agent": config.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9",
        },
        timeout=httpx.Timeout(config.timeout_s, connect=10.0),
        follow_redirects=True,
        max_redirects=10,
        http2=_http2_available(),
        limits=httpx.Limits(max_connections=config.concurrency + 2),
    )
