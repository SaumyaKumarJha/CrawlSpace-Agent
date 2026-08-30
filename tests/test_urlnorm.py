from crawlspace.urlnorm import (
    normalise, same_site, looks_non_html, registrable_host, path_depth,
)


class TestNormalise:
    def test_drops_fragment(self):
        assert normalise("https://a.com/p#top") == "https://a.com/p"

    def test_lowercases_host_not_path(self):
        assert normalise("https://A.COM/Path") == "https://a.com/Path"

    def test_strips_default_port(self):
        assert normalise("https://a.com:443/p") == "https://a.com/p"
        assert normalise("http://a.com:80/p") == "http://a.com/p"

    def test_keeps_nonstandard_port(self):
        assert normalise("https://a.com:8443/p") == "https://a.com:8443/p"

    def test_empty_path_becomes_slash(self):
        assert normalise("https://a.com") == "https://a.com/"

    def test_strips_tracking_params(self):
        assert normalise("https://a.com/p?utm_source=fb&id=3") == "https://a.com/p?id=3"
        assert normalise("https://a.com/p?gclid=xyz") == "https://a.com/p"

    def test_sorts_params_so_order_does_not_duplicate(self):
        assert normalise("https://a.com/p?b=2&a=1") == normalise("https://a.com/p?a=1&b=2")

    def test_resolves_relative_against_base(self):
        assert normalise("/about", base="https://a.com/blog/x") == "https://a.com/about"
        assert normalise("../y", base="https://a.com/blog/x/") == "https://a.com/blog/y"

    def test_rejects_non_http_schemes(self):
        for bad in ("mailto:x@y.com", "tel:+91", "javascript:void(0)", "#anchor", ""):
            assert normalise(bad) is None

    def test_collapses_duplicate_slashes(self):
        assert normalise("https://a.com//blog//x") == "https://a.com/blog/x"


class TestScope:
    def test_same_site_ignores_www(self):
        assert same_site("https://www.a.com/x", "https://a.com/")
        assert same_site("https://a.com/x", "https://www.a.com/")

    def test_subdomains_are_in_scope(self):
        assert same_site("https://shop.a.com/x", "https://a.com/")

    def test_other_domains_are_not(self):
        assert not same_site("https://b.com/x", "https://a.com/")
        # the classic bug: suffix matching without the dot
        assert not same_site("https://nota.com/x", "https://a.com/")

    def test_registrable_host(self):
        assert registrable_host("https://www.a.com/x") == "a.com"


class TestFilters:
    def test_detects_non_html(self):
        for u in ("https://a.com/x.pdf", "https://a.com/i.JPG", "https://a.com/s.css"):
            assert looks_non_html(u)

    def test_html_passes(self):
        for u in ("https://a.com/x", "https://a.com/x.html", "https://a.com/p?f=1.pdf"):
            assert not looks_non_html(u)

    def test_path_depth(self):
        assert path_depth("https://a.com/") == 0
        assert path_depth("https://a.com/a/b/c") == 3
