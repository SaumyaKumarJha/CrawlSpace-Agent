"""HTTP fetch layer: one URL in, one PageRecord out.

Stops at the raw response and the link list. Rendering lives in renderer.py,
field extraction in parse.py — keeping them apart is what lets each be tested
without the others existing.
"""
from __future__ import annotations

import time

import httpx

from .models import CrawlConfig, PageRecord
from .parse import links_for_frontier
from .politeness import RateLimiter, RobotsGate

RETRY_STATUS = {429, 500, 502, 503, 504}


class Fetcher:
    def __init__(self, config: CrawlConfig, limiter: RateLimiter, robots: RobotsGate) -> None:
        self.config = config
        self.limiter = limiter
        self.robots = robots

    async def fetch(self, client: httpx.AsyncClient, url: str, *, depth: int,
                    sources: list[str]) -> tuple[PageRecord, list[str], bytes]:
        """Returns the record, the links found in the raw HTML, and the body.

        The body comes back so the caller can store it without a second
        request — re-fetching a client's page just to keep a copy is both rude
        and slow.
        """
        record = PageRecord(url=url, depth=depth, discovered_via=sources)

        if not self.robots.allowed(url):
            record.fetch_error = "blocked_by_robots"
            return record, [], b""

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
                    return record, [], b""
                continue

            if response.status_code in RETRY_STATUS and attempt < 2:
                # Respect Retry-After when the server bothers to send one.
                retry_after = response.headers.get("retry-after")
                if retry_after and retry_after.isdigit():
                    self.limiter.set_min_interval(min(float(retry_after), 10.0))
                continue
            break

        if response is None:
            return record, [], b""

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
            return record, [], b""

        body = response.content
        record.raw_html_len = len(body)
        return record, links_for_frontier(body, record.final_url or url), body


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
