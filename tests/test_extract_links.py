"""Frontier link extraction — the edge cases that break crawlers."""
from crawlspace.parse import links_for_frontier

PAGE = "https://a.com/blog/post"


def test_plain_hrefs_pass_through_unresolved():
    # The frontier resolves them; keeping them raw here keeps this function pure.
    assert links_for_frontier('<a href="/x">X</a><a href="y.html">Y</a>', PAGE) == [
        "/x", "y.html"
    ]


def test_base_href_rewrites_relative_links():
    html = '<head><base href="https://cdn.a.com/v2/"></head><a href="x.html">X</a>'
    assert links_for_frontier(html, PAGE) == ["https://cdn.a.com/v2/x.html"]


def test_relative_base_resolves_against_the_page():
    html = '<head><base href="/shop/"></head><a href="ghee">G</a>'
    assert links_for_frontier(html, PAGE) == ["https://a.com/shop/ghee"]


def test_absolute_links_survive_a_base_tag():
    html = '<head><base href="/shop/"></head><a href="https://b.com/x">X</a>'
    assert links_for_frontier(html, PAGE) == ["https://b.com/x"]


def test_malformed_markup_does_not_crash():
    assert links_for_frontier("<a href=/x>X<a href='/y'>Y", PAGE) == ["/x", "/y"]


def test_anchor_without_href_ignored():
    assert links_for_frontier('<a name="top">t</a><a href="/x">X</a>', PAGE) == ["/x"]


def test_duplicates_are_kept():
    # Dedupe is the frontier's job, and link count matters to later checks.
    assert links_for_frontier('<a href="/x">1</a><a href="/x">2</a>', PAGE) == ["/x", "/x"]


def test_accepts_bytes():
    assert links_for_frontier(b'<a href="/x">x</a>', PAGE) == ["/x"]


def test_empty_input():
    assert links_for_frontier("", PAGE) == []
