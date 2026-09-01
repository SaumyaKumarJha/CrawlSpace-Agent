"""Robots compliance and rate limiting.

Two separate concerns, kept in one module because they are the two things that
decide whether a client's host ever blocks us.
"""
from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit, urlunsplit

import httpx
from protego import Protego


class RateLimiter:
    """Global token-bucket limiter shared by every worker.

    Deliberately global rather than per-worker: three workers each doing
    1.5 req/s is 4.5 req/s at the server, which is how you get a 429.
    """

    def __init__(self, requests_per_second: float) -> None:
        self._interval = 1.0 / max(requests_per_second, 0.05)
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    def set_min_interval(self, seconds: float) -> None:
        """Slow down, never speed up — used to honour robots crawl-delay."""
        self._interval = max(self._interval, seconds)

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_at = now + self._interval


class RobotsGate:
    """robots.txt parsing, per host.

    Fails open on a network error and closed on an explicit 401/403, which is
    what Google does: an unreachable robots.txt is not permission to crawl a
    site that is actively refusing you.
    """

    def __init__(self, user_agent: str, *, obey: bool = True) -> None:
        self.user_agent = user_agent
        self.obey = obey
        self._parsers: dict[str, Protego | None] = {}
        self._sitemaps: dict[str, list[str]] = {}
        self._blocked: set[str] = set()

    @staticmethod
    def _origin(url: str) -> str:
        p = urlsplit(url)
        return urlunsplit((p.scheme, p.netloc, "", "", ""))

    async def load(self, client: httpx.AsyncClient, url: str) -> None:
        origin = self._origin(url)
        if origin in self._parsers:
            return
        robots_url = origin + "/robots.txt"
        try:
            r = await client.get(robots_url, timeout=10.0)
        except Exception:
            # Broad deliberately, same reasoning as fetcher.py: robots.txt is
            # fetched before any page and before the crawl's per-page
            # recovery exists, so a non-httpx exception here (h2, ssl, anyio)
            # must fail open like any other unreachable robots.txt, not crash
            # the run before it starts.
            self._parsers[origin] = None
            self._sitemaps[origin] = []
            return

        if r.status_code in (401, 403):
            self._parsers[origin] = None
            self._blocked.add(origin)
            self._sitemaps[origin] = []
            return
        if r.status_code >= 400:
            self._parsers[origin] = None
            self._sitemaps[origin] = []
            return

        try:
            parser = Protego.parse(r.text)
            self._parsers[origin] = parser
            self._sitemaps[origin] = list(parser.sitemaps)
        except Exception:
            # A malformed robots.txt some site actually serves in production
            # should fail open, not crash the run before any page is fetched.
            self._parsers[origin] = None
            self._sitemaps[origin] = []

    def allowed(self, url: str) -> bool:
        if not self.obey:
            return True
        origin = self._origin(url)
        if origin in self._blocked:
            return False
        parser = self._parsers.get(origin)
        if parser is None:
            return True
        return parser.can_fetch(url, self.user_agent)

    def crawl_delay(self, url: str) -> float | None:
        parser = self._parsers.get(self._origin(url))
        if parser is None:
            return None
        delay = parser.crawl_delay(self.user_agent)
        return float(delay) if delay else None

    def sitemaps(self, url: str) -> list[str]:
        return self._sitemaps.get(self._origin(url), [])
