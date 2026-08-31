from crawlspace.traps import is_trap, parameter_signature

OK = [
    "https://a.com/",
    "https://a.com/products/ghee",
    "https://a.com/blog/why-bilona",
    "https://a.com/shop?page=3",
    "https://a.com/search?q=ghee",
    "https://a.com/shop?color=red&size=xl",       # two facets is a real page
]

TRAPPED = [
    "https://a.com/calendar/2031/07",
    "https://a.com/events/2029",
    "https://a.com/cart",
    "https://a.com/checkout/",
    "https://a.com/my-account",
    "https://a.com/wp-admin/",
    "https://a.com/feed/",
    "https://a.com/shop?color=red&size=xl&brand=acme&sort=price",
    "https://a.com/a/b/c/d/e/f/g/h/i/j",
    "https://a.com/shop/shop/shop/product",
    "https://a.com/blog/page/450",
]


def test_normal_urls_pass():
    for url in OK:
        trapped, reason = is_trap(url)
        assert not trapped, f"{url} wrongly flagged: {reason}"


def test_traps_are_caught():
    for url in TRAPPED:
        trapped, reason = is_trap(url)
        assert trapped, f"{url} not flagged"
        assert reason


def test_reason_is_always_given():
    trapped, reason = is_trap("https://a.com/cart")
    assert trapped and len(reason) > 5


class TestParameterSignature:
    def test_ignores_values(self):
        a = parameter_signature("https://a.com/s?color=red&size=xl")
        b = parameter_signature("https://a.com/s?color=blue&size=s")
        assert a == b == "color&size"

    def test_order_independent(self):
        assert (parameter_signature("https://a.com/s?b=1&a=2")
                == parameter_signature("https://a.com/s?a=2&b=1"))

    def test_no_query_is_empty(self):
        assert parameter_signature("https://a.com/s") == ""
