import asyncio, time
import httpx
import pytest

from crawlspace.politeness import RateLimiter, RobotsGate

UA = "CrawlspaceBot"
ROBOTS = """
User-agent: *
Disallow: /admin/
Crawl-delay: 2
Sitemap: https://a.com/sitemap.xml
"""


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_robots_blocks_and_allows():
    async with _client(lambda r: httpx.Response(200, text=ROBOTS)) as c:
        gate = RobotsGate(UA)
        await gate.load(c, "https://a.com/")
        assert gate.allowed("https://a.com/products")
        assert not gate.allowed("https://a.com/admin/x")
        assert gate.crawl_delay("https://a.com/") == 2.0
        assert gate.sitemaps("https://a.com/") == ["https://a.com/sitemap.xml"]


async def test_a_non_httpx_exception_fetching_robots_fails_open_not_crashes():
    """robots.txt is fetched before any page and before the crawl's per-page
    recovery exists. Before the fix, only httpx.HTTPError was caught here —
    a raw h2/ssl/anyio exception would crash the whole run before it started.
    """
    class ProtocolLevelError(Exception):
        pass

    def handler(request):
        raise ProtocolLevelError("malformed frame")

    async with _client(handler) as c:
        gate = RobotsGate(UA)
        await gate.load(c, "https://a.com/")          # must not raise
        assert gate.allowed("https://a.com/anything")  # fails open


async def test_malformed_robots_txt_content_fails_open_not_crashes():
    """Protego is generally lenient, but a site can serve genuine garbage as
    robots.txt — this must never be the thing that takes down the crawl."""
    async with _client(lambda r: httpx.Response(200, content=b"\xff\xfe\x00garbage")) as c:
        gate = RobotsGate(UA)
        await gate.load(c, "https://a.com/")
        assert gate.allowed("https://a.com/anything")


async def test_missing_robots_fails_open():
    async with _client(lambda r: httpx.Response(404)) as c:
        gate = RobotsGate(UA)
        await gate.load(c, "https://a.com/")
        assert gate.allowed("https://a.com/anything")


async def test_forbidden_robots_fails_closed():
    async with _client(lambda r: httpx.Response(403)) as c:
        gate = RobotsGate(UA)
        await gate.load(c, "https://a.com/")
        assert not gate.allowed("https://a.com/anything")


async def test_rate_limiter_spaces_requests():
    lim = RateLimiter(requests_per_second=20)  # 50ms apart
    start = time.monotonic()
    await asyncio.gather(*(lim.acquire() for _ in range(4)))
    assert time.monotonic() - start >= 0.14


async def test_crawl_delay_only_slows_down():
    lim = RateLimiter(requests_per_second=1)  # 1.0s
    lim.set_min_interval(0.1)                 # must not speed us up
    assert lim._interval == 1.0
