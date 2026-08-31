from crawlspace.sitemaps import parse_sitemap_xml
import gzip

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
