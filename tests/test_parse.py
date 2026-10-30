from pathlib import Path

import pytest
from selectolax.parser import HTMLParser

from crawlspace import parse
from crawlspace.models import PageRecord

FIXTURES = Path(__file__).parent / "fixtures" / "pages"
URL = "https://example.com/products/ghee"
ROOT = "https://example.com/"


def tree(name: str) -> HTMLParser:
    return HTMLParser((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def product():
    return tree("product.html")


@pytest.fixture
def bare():
    return tree("bare.html")


class TestTitle:
    def test_extracts_and_collapses_whitespace(self, product):
        t = parse.extract_title(product)
        assert t["text"] == "A2 Bilona Ghee | Green Organics"

    def test_reports_pixel_width_not_just_length(self, product):
        t = parse.extract_title(product)
        assert t["len"] == 31
        assert 250 < t["px_width"] < 350

    def test_absent_title_is_none(self, bare):
        assert parse.extract_title(bare) is None

    def test_pixel_width_distinguishes_same_length_strings(self):
        narrow = parse.pixel_width("llllllllllllll")
        wide = parse.pixel_width("MMMMMMMMMMMMMM")
        assert wide > narrow * 2


class TestMetaAndCanonical:
    def test_description(self, product):
        d = parse.extract_meta_description(product)
        assert d["text"].startswith("Hand-churned A2 ghee")
        assert d["len"] == len(d["text"])

    def test_absent_description_is_none(self, bare):
        assert parse.extract_meta_description(bare) is None

    def test_canonical_is_resolved_to_absolute(self, product):
        assert parse.extract_canonical(product, URL) == URL

    def test_absent_canonical_is_none(self, bare):
        assert parse.extract_canonical(bare, URL) is None

    def test_robots_meta_lowercased(self, product):
        assert parse.extract_robots_meta(product) == "index, follow"

    def test_absent_robots_meta_is_none(self, bare):
        assert parse.extract_robots_meta(bare) is None


class TestHeadings:
    def test_h1_list_and_outline(self, product):
        h1s, outline = parse.extract_headings(product)
        assert h1s == ["A2 Bilona Ghee"]
        assert outline == [("h1", 1), ("h2", 2), ("h3", 3)]

    def test_no_headings_is_empty_not_error(self, bare):
        h1s, outline = parse.extract_headings(bare)
        assert h1s == [] and outline == []


class TestWordCount:
    def test_counts_main_content_only(self, product):
        # Main content is ~24 words. Nav, header and footer must not inflate it.
        count = parse.extract_word_count(product)
        assert 18 < count < 40

    def test_script_contents_are_not_words(self, product):
        assert parse.extract_word_count(product) < 60

    def test_counting_does_not_mutate_the_tree(self, product):
        parse.extract_word_count(product)
        # If strip_tags had run on the live tree, the nav links would be gone.
        internal, _ = parse.extract_links(product, URL, ROOT)
        assert any(link.href.endswith("/shop") for link in internal)


class TestLinks:
    def test_internal_external_split(self, product):
        internal, external = parse.extract_links(product, URL, ROOT)
        assert any(l.href.endswith("/products/oil") for l in internal)
        assert any("instagram.com" in l.href for l in external)

    def test_relative_links_resolved_against_page(self, product):
        internal, _ = parse.extract_links(product, URL, ROOT)
        assert all(l.href.startswith("https://") for l in internal)

    def test_nofollow_detected(self, product):
        _, external = parse.extract_links(product, URL, ROOT)
        insta = next(l for l in external if "instagram" in l.href)
        assert insta.nofollow is True

    def test_anchor_text_captured(self, product):
        internal, _ = parse.extract_links(product, URL, ROOT)
        oil = next(l for l in internal if l.href.endswith("/products/oil"))
        assert oil.anchor == "Cold pressed oils"

    def test_mailto_is_dropped(self, product):
        internal, external = parse.extract_links(product, URL, ROOT)
        assert not any("mailto" in l.href for l in internal + external)


class TestImages:
    def test_finds_all_images(self, product):
        assert len(parse.extract_images(product, URL)) == 3

    def test_missing_alt_is_none_empty_alt_is_empty_string(self, product):
        images = parse.extract_images(product, URL)
        by_src = {i.src.rsplit("/", 1)[-1]: i for i in images}
        assert by_src["ghee.jpg"].alt == "Jar of A2 bilona ghee"
        assert by_src["divider.png"].alt == ""      # deliberately decorative
        assert by_src["farm.jpg"].alt is None       # attribute genuinely absent

    def test_lazy_flag(self, product):
        images = {i.src.rsplit("/", 1)[-1]: i for i in parse.extract_images(product, URL)}
        assert images["farm.jpg"].lazy is True
        assert images["ghee.jpg"].lazy is False


class TestHreflangAndOg:
    def test_hreflang_pairs(self, product):
        tags = parse.extract_hreflang(product, URL)
        assert {t["lang"] for t in tags} == {"en-in", "hi-in"}

    def test_og_and_twitter(self, product):
        og = parse.extract_og(product)
        assert og["og:title"] == "A2 Bilona Ghee"
        assert og["twitter:card"] == "summary_large_image"

    def test_bare_page_has_no_og(self, bare):
        assert parse.extract_og(bare) == {}


class TestStructuredData:
    def test_json_ld_object(self, product):
        data = parse.extract_structured_data(product)
        product_node = next(d for d in data if d.get("@type") == "Product")
        assert product_node["offers"]["price"] == "899"

    def test_graph_is_flattened(self):
        data = parse.extract_structured_data(tree("graph_jsonld.html"))
        types = {d.get("@type") for d in data}
        assert {"Organization", "WebSite", "BreadcrumbList"} <= types

    def test_microdata_extracted(self):
        data = parse.extract_structured_data(tree("graph_jsonld.html"))
        local = next(d for d in data if d["_format"] == "microdata")
        assert local["@type"] == "LocalBusiness"
        assert local["name"] == "Green Organics"

    def test_invalid_json_ld_is_recorded_not_dropped(self):
        data = parse.extract_structured_data(tree("broken_jsonld.html"))
        assert len(data) == 1
        assert data[0]["_invalid"] is True

    def test_bare_page_has_no_structured_data(self, bare):
        assert parse.extract_structured_data(bare) == []


class TestParseInto:
    def test_fills_a_record(self):
        record = PageRecord(url=URL, final_url=URL, status=200,
                            content_type="text/html")
        parse.parse_into(record, (FIXTURES / "product.html").read_text(), ROOT)
        assert record.title["text"] == "A2 Bilona Ghee | Green Organics"
        assert record.h1 == ["A2 Bilona Ghee"]
        assert record.canonical == URL
        assert record.word_count > 0
        assert len(record.images) == 3
        assert record.structured_data

    def test_empty_html_is_a_no_op(self):
        record = PageRecord(url=URL)
        parse.parse_into(record, "", ROOT)
        assert record.title is None
        assert record.word_count == 0

    def test_resolves_against_final_url_after_redirect(self):
        record = PageRecord(url="https://example.com/old-ghee",
                            final_url=URL, status=200, content_type="text/html")
        parse.parse_into(record, (FIXTURES / "product.html").read_text(), ROOT)
        # Canonical is href="/products/ghee" — resolving against the *requested*
        # URL would still give the same string here, but resolving against a
        # deeper redirect target is where this goes wrong in the wild.
        assert record.canonical == URL


class TestLinksForFrontier:
    def test_returns_raw_hrefs(self, product):
        html = (FIXTURES / "product.html").read_text()
        hrefs = parse.links_for_frontier(html)
        assert "/products/oil" in hrefs
        assert "mailto:hi@example.com" in hrefs  # frontier normalises it away

    def test_accepts_bytes(self):
        assert parse.links_for_frontier(b'<a href="/x">x</a>') == ["/x"]

    def test_empty_input(self):
        assert parse.links_for_frontier("") == []
