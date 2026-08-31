from crawlspace.htmlstore import HtmlStore

URL = "https://a.com/products/ghee"


def test_roundtrip(tmp_path):
    store = HtmlStore(tmp_path)
    store.put("raw", URL, "<html>hello</html>")
    assert store.get("raw", URL) == "<html>hello</html>"


def test_kinds_are_separate(tmp_path):
    store = HtmlStore(tmp_path)
    store.put("raw", URL, "<p>raw</p>")
    store.put("rendered", URL, "<p>rendered</p>")
    assert store.get("raw", URL) == "<p>raw</p>"
    assert store.get("rendered", URL) == "<p>rendered</p>"


def test_missing_returns_none(tmp_path):
    assert HtmlStore(tmp_path).get("raw", URL) is None


def test_key_is_stable_and_url_specific():
    assert HtmlStore.key(URL) == HtmlStore.key(URL)
    assert HtmlStore.key(URL) != HtmlStore.key(URL + "?x=1")


def test_accepts_bytes(tmp_path):
    store = HtmlStore(tmp_path)
    store.put("raw", URL, b"<html>bytes</html>")
    assert store.get("raw", URL) == "<html>bytes</html>"
