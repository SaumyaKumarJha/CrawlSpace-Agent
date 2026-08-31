from crawlspace.frontier import Frontier

ROOT = "https://a.com/"


def make():
    return Frontier(ROOT, max_depth=3, max_pages=10)


def test_seed_is_queued_once():
    f = make()
    assert f.add(ROOT, depth=0, source="seed")
    assert not f.add(ROOT, depth=0, source="seed")
    assert len(f) == 1


def test_equivalent_urls_dedupe():
    f = make()
    f.add("https://a.com/p?utm_source=x", depth=1, source="internal_link")
    added = f.add("https://a.com/p#frag", depth=1, source="sitemap")
    assert not added
    assert f.seen_count == 1


def test_dedupe_still_records_extra_source():
    f = make()
    f.add("https://a.com/p", depth=1, source="internal_link")
    f.add("https://a.com/p", depth=1, source="sitemap")
    assert f.sources_for("https://a.com/p") == ["internal_link", "sitemap"]


def test_offsite_rejected():
    f = make()
    assert not f.add("https://other.com/x", depth=1, source="internal_link")
    assert f.stats()["skipped_offsite"] == 1


def test_depth_cap():
    f = make()
    assert not f.add("https://a.com/deep", depth=4, source="internal_link")
    assert f.stats()["skipped_depth"] == 1


def test_breadth_first_order():
    f = make()
    f.add("https://a.com/deep", depth=3, source="internal_link")
    f.add("https://a.com/shallow", depth=1, source="internal_link")
    assert f.pop().url == "https://a.com/shallow"


def test_page_cap_stops_dispatch():
    f = Frontier(ROOT, max_depth=5, max_pages=2)
    for i in range(5):
        f.add(f"https://a.com/{i}", depth=1, source="internal_link")
    assert f.pop() is not None
    assert f.pop() is not None
    assert f.pop() is None


class TestOrphansAndTraps:
    def test_sitemap_only_url_is_an_orphan(self):
        f = make()
        f.add("https://a.com/hidden", depth=1, source="sitemap")
        assert f.orphans({"https://a.com/hidden"}) == ["https://a.com/hidden"]

    def test_linked_url_is_not_an_orphan(self):
        f = make()
        f.add("https://a.com/p", depth=1, source="sitemap")
        f.add("https://a.com/p", depth=1, source="internal_link")
        assert f.orphans({"https://a.com/p"}) == []

    def test_uncrawled_url_is_not_reported_as_orphan(self):
        f = make()
        f.add("https://a.com/hidden", depth=1, source="sitemap")
        assert f.orphans(set()) == []

    def test_trap_urls_are_rejected(self):
        f = make()
        assert not f.add("https://a.com/cart", depth=1, source="internal_link")
        assert f.stats()["skipped_traps"] == 1

    def test_trap_reason_is_recorded(self):
        f = make()
        f.add("https://a.com/checkout/", depth=1, source="internal_link")
        assert f.trap_reasons
