# Crawlspace — Day 3

`parse.py`. HTML into PageRecord fields. The largest single file in the build,
and the one everything downstream trusts.

## Run

Nothing new to install. Same command:

```bash
python -m crawlspace.cli crawl newbi.live --pages 50
```

`pages.jsonl` now carries titles, meta descriptions, canonicals, headings,
word counts, links, images, hreflang, Open Graph and structured data.

## What changed

| file | change |
|---|---|
| `parse.py` | **new** — every field extractor, pure, no network |
| `textwidth.py` | **new** — title width in pixels, Arial metrics |
| `crawler.py` | parses each page, preferring the rendered DOM |
| `fetcher.py` | regex link scaffold **deleted**; uses `parse.links_for_frontier` |
| `tests/test_parse.py` | **new** — 40 tests against four fixture pages |
| `tests/test_extract_links.py` | rewritten for the real extractor |

## Decisions worth knowing

**Parsing runs on the rendered DOM, not the raw HTML.** A Wix product page has
almost nothing in its raw HTML — parsing that would report an empty page that
is not empty. Raw is the fallback when rendering was skipped or failed.

**Titles are measured in pixels, not characters.** Google truncates on rendered
width. "Illinois Wills" and "MMMMMMMMMMMMMM" are both 14 characters; one is
103px and the other 233px. `textwidth.py` holds Arial advance widths and
reports both, so check ONP-02 can use the number that actually matters.

**alt=None and alt="" are different things.** A missing alt attribute is a
failure; `alt=""` is the correct, deliberate marker for a decorative image.
selectolax stores both as `None`, which would have merged them — `_attr()`
tests attribute *presence* against the key list instead. A test caught this;
without it, every well-built client site would have failed the alt-text check.

**Invalid JSON-LD is recorded, not skipped.** Malformed structured data is
check SDA-04. Silently dropping it would hide the exact thing being looked for,
so a parse failure becomes `{"_invalid": true, "_error": ...}`.

**No extruct.** The plan named it; it needs a compiler and fails to build on
Windows without one. JSON-LD and microdata are extracted natively instead —
about 80 lines, no dependency, and covers what the SDA checks need. RDFa is not
supported, which is fine: it is vanishingly rare on the sites we audit.

**Word count strips boilerplate on a copy of the tree.** Stripping nav, header,
footer and scripts from the live tree would delete the links every later
extractor needs. There is a test for that specific mistake.

**Relative URLs resolve against `final_url`, not `url`.** After a redirect,
resolving against the requested URL produces phantom broken links.

**`<base href>` is honoured.** It changes what every relative link on the page
points at, and ignoring one sends the crawler to URLs that do not exist.

## Verify

```bash
pytest tests/ -q                       # 80 tests
python tests/fixtures/serve.py 8899    # terminal 1
python -m crawlspace.cli crawl http://127.0.0.1:8899/ --pages 25 --rps 20
```

Expected, and verified:

| page | title | h1 | words |
|---|---|---|---|
| `/js-page.html` | JS Page | Rendered by JavaScript | **840** |
| `/about.html` | About | About | 4 |

`js-page.html` has two words in its raw HTML. 840 is the rendered count —
proof that parsing runs after the browser, not before it.

Note also that `/` and `/index.html` both come back with the title "Home".
That is a real duplicate-title finding sitting in the data, waiting for the
check that reports it. Nothing reports it yet — that is day 9.

## Day 1 debt now cleared

- The regex link scaffold in `fetcher.py` is gone, as promised.
- `HtmlStore` earns its keep: re-parsing is instant, no re-crawl.

## Next: day 4

Sitemap parsing, orphan detection, trap patterns, and the template signature —
the DOM-shape hash that turns "142 pages missing meta descriptions" into "your
product template is missing one".
