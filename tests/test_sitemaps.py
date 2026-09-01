import gzip

import httpx

from crawlspace.sitemaps import _exists, collect_sitemaps, parse_sitemap_xml

URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://a.com/</loc><lastmod>2026-01-01</lastmod></url>
  <url><loc>https://a.com/about</loc></url>
</urlset>"""

INDEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://a.com/sitemap-products.xml</loc></sitemap>
  <sitemap><loc>https://a.com/sitemap-posts.xml</loc></sitemap>
</sitemapindex>"""


def test_parses_urlset():
    pages, nested = parse_sitemap_xml(URLSET)
    assert pages == ["https://a.com/", "https://a.com/about"]
    assert nested == []


def test_parses_sitemap_index_separately():
    pages, nested = parse_sitemap_xml(INDEX)
    assert pages == []
    assert len(nested) == 2


def test_handles_gzip():
    pages, _ = parse_sitemap_xml(gzip.compress(URLSET))
    assert len(pages) == 2


def test_malformed_xml_returns_empty_not_crash():
    assert parse_sitemap_xml(b"<urlset><url><loc>oops") == ([], [])


def test_html_served_instead_of_xml():
    assert parse_sitemap_xml(b"<!doctype html><html><body>404</body></html>") == ([], [])


def test_empty_input():
    assert parse_sitemap_xml(b"") == ([], [])


class _ProtocolLevelError(Exception):
    """Stands in for an h2/ssl/anyio exception, not an httpx.HTTPError."""


async def test_collect_sitemaps_survives_a_non_httpx_exception():
    """Before the fix, only httpx.HTTPError was caught per sitemap URL — a
    raw h2/ssl/anyio exception would propagate out of collect_sitemaps and
    crash the run before a single page was fetched."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise _ProtocolLevelError("malformed frame")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await collect_sitemaps(client, "https://a.com/",
                                        ["https://a.com/sitemap.xml"])

    assert report.urls == []
    assert any("_ProtocolLevelError" in e["error"] for e in report.errors)


async def test_exists_survives_a_non_httpx_exception():
    def handler(request: httpx.Request) -> httpx.Response:
        raise _ProtocolLevelError("malformed frame")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await _exists(client, "https://a.com/sitemap.xml") is False
