"""Playwright rendering — the second half of the dual capture.

Day 1 gave us the raw HTTP response. This module gives us the DOM after the
page's JavaScript has run. The gap between the two is check ONP-10, and on a
Wix or React site it is usually the most valuable finding in the audit.

One browser per crawl, a pool of pages inside it. Launching Chromium costs
about a second; doing it per URL would dominate the crawl.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from playwright.async_api import (
    Browser,
    Error as PlaywrightError,
    Page,
    async_playwright,
)

# Assets we never need. The DOM is the product here, not the pixels — blocking
# these roughly triples crawl speed and spares the client's bandwidth.
BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}


@dataclass(slots=True)
class RenderResult:
    html: str
    timed_out: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.html) and self.error is None


class Renderer:
    """Async context manager owning one Chromium instance and a page pool."""

    def __init__(self, user_agent: str, *, concurrency: int = 3,
                 timeout_s: float = 15.0, block_assets: bool = True) -> None:
        self.user_agent = user_agent
        self.concurrency = concurrency
        self.timeout_ms = int(timeout_s * 1000)
        self.block_assets = block_assets
        self._pw = None
        self._browser: Browser | None = None
        self._pool: asyncio.Queue[Page] | None = None

    async def __aenter__(self) -> "Renderer":
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        context = await self._browser.new_context(
            user_agent=self.user_agent,
            viewport={"width": 1366, "height": 900},
            java_script_enabled=True,
            ignore_https_errors=True,
        )
        if self.block_assets:
            await context.route("**/*", self._route)

        self._pool = asyncio.Queue()
        for _ in range(self.concurrency):
            self._pool.put_nowait(await context.new_page())
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()

    @staticmethod
    async def _route(route, request) -> None:
        if request.resource_type in BLOCKED_RESOURCE_TYPES:
            await route.abort()
        else:
            await route.continue_()

    async def render(self, url: str) -> RenderResult:
        """Load url, wait for the network to settle, return the DOM.

        A timeout is not a failure. A slow page still has a DOM worth reading,
        so we capture whatever rendered and flag it — one stuck page must never
        cost us the page itself.
        """
        assert self._pool is not None, "Renderer used outside its context manager"
        page = await self._pool.get()
        try:
            timed_out = False
            try:
                await page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)
            except PlaywrightError as exc:
                if "Timeout" not in str(exc):
                    return RenderResult(html="", error=f"{type(exc).__name__}: {exc}")
                timed_out = True

            try:
                html = await page.content()
            except PlaywrightError as exc:
                return RenderResult(html="", timed_out=timed_out,
                                    error=f"content: {exc}")

            return RenderResult(html=html, timed_out=timed_out)
        finally:
            self._pool.put_nowait(page)
