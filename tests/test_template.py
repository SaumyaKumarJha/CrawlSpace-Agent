from crawlspace.template import signature, group_by_template, representative_urls
from crawlspace.models import PageRecord

PRODUCT = """
<html><body><div class="page">
  <div class="product"><h1>{name}</h1><p class="price">{price}</p>
  <img src="{img}"><button class="buy">Buy</button></div>
</div></body></html>
"""

ARTICLE = """
<html><body><div class="page">
  <article class="post"><h1>{name}</h1><p>{body}</p>
  <div class="author">By someone</div></article>
</div></body></html>
"""


def test_same_template_different_content_matches():
    a = signature(PRODUCT.format(name="Ghee", price="899", img="/a.jpg"))
    b = signature(PRODUCT.format(name="Mustard Oil", price="349", img="/b.jpg"))
    assert a == b


def test_different_templates_differ():
    a = signature(PRODUCT.format(name="Ghee", price="899", img="/a.jpg"))
    b = signature(ARTICLE.format(name="Why bilona", body="Because."))
    assert a != b


def test_volatile_class_names_do_not_split_a_template():
    a = signature('<html><body><div class="card css-1a2b3c4"><p>x</p></div></body></html>')
    b = signature('<html><body><div class="card css-9z8y7x6"><p>y</p></div></body></html>')
    assert a == b


def test_state_classes_do_not_split_a_template():
    a = signature('<html><body><div class="tab is-active"><p>x</p></div></body></html>')
    b = signature('<html><body><div class="tab"><p>y</p></div></body></html>')
    assert a == b


def test_similar_repeat_counts_group_together():
    eight = "".join('<li class="card"><a href="#">x</a></li>' for _ in range(8))
    twelve = "".join('<li class="card"><a href="#">x</a></li>' for _ in range(12))
    assert signature(f"<html><body><ul>{eight}</ul></body></html>") == \
           signature(f"<html><body><ul>{twelve}</ul></body></html>")


def test_signature_is_stable_across_calls():
    html = PRODUCT.format(name="Ghee", price="899", img="/a.jpg")
    assert signature(html) == signature(html)


def test_empty_html_is_none():
    assert signature("") is None


def _rec(url, sig, depth=1):
    r = PageRecord(url=url, final_url=url, status=200, content_type="text/html",
                   depth=depth)
    r.template_signature = sig
    return r


class TestGrouping:
    def test_groups_by_signature(self):
        records = [_rec("https://a.com/p/1", "aaa"), _rec("https://a.com/p/2", "aaa"),
                   _rec("https://a.com/blog/1", "bbb")]
        groups = group_by_template(records)
        assert len(groups) == 2
        assert len(groups["aaa"]) == 2

    def test_representatives_one_per_template(self):
        records = [_rec("https://a.com/p/1", "aaa"), _rec("https://a.com/p/2", "aaa"),
                   _rec("https://a.com/blog/1", "bbb")]
        reps = representative_urls(records)
        assert len(reps) == 2

    def test_representatives_prefer_shallow_pages(self):
        records = [_rec("https://a.com/deep/p", "aaa", depth=4),
                   _rec("https://a.com/p", "aaa", depth=1)]
        assert representative_urls(records) == ["https://a.com/p"]

    def test_representatives_are_capped(self):
        records = [_rec(f"https://a.com/{i}", f"sig{i}") for i in range(30)]
        assert len(representative_urls(records, limit=12)) == 12
