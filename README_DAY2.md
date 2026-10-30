# Crawlspace — Day 2

Playwright rendering and the dual capture. The crawler now sees each page
twice: as the server sent it, and as a browser builds it.

## One extra install step

```bash
pip install -e ".[dev]"
playwright install chromium        # downloads the browser, ~150MB, once
```

## Run

```bash
python -m crawlspace.cli crawl newbi.live --pages 50
python -m crawlspace.cli crawl example.com --pages 100 --rps 1

python -m crawlspace.cli crawl newbi.live --no-render   # day-1 behaviour
```

New in the summary:

```
  rendered          46
  render timeouts   2
  render failures   0
  JS-dependent      31   (rendered DOM >3x raw HTML — preview of check ONP-10)
```

## What changed

| file | change |
|---|---|
| `renderer.py` | **new** — one Chromium, a pool of pages, images/fonts/media blocked |
| `htmlstore.py` | **new** — gzipped raw and rendered HTML on disk, keyed by URL hash |
| `crawler.py` | renders every HTML 200, merges rendered links into the frontier |
| `fetcher.py` | `fetch()` now returns the body too, so storing it costs no extra request |
| `cli.py` | `--no-render`, `--render-timeout`, render stats in the summary |
| `models.py` | `CrawlConfig.render_timeout_s` |

## Why it matters

Two things only work from today:

**Pages that only exist in JavaScript.** A Wix or React nav is built at
runtime, so those links are invisible to a raw-HTML crawler. The renderer
finds them, and the frontier is fed from the union of both link sets.

**The raw-vs-rendered gap.** `raw_html_len` and `rendered_html_len` now both
have values. Where the second is more than three times the first, the page's
content depends on JavaScript — that is check ONP-10, and on a Wix site it is
usually the single most valuable finding in the audit.

## Acceptance test

```bash
python tests/fixtures/serve.py 8899        # terminal 1
python -m crawlspace.cli crawl http://127.0.0.1:8899/ --pages 25 --rps 20
```

Expected — and verified:

| page | raw | rendered | ratio |
|---|---|---|---|
| `/about.html` | 161 | 161 | 1.0x |
| `/blog/post-1.html` | 123 | 123 | 1.0x |
| `/js-page.html` | 624 | 5,806 | **9.3x** |

Static pages show no gap; the JavaScript page shows a 9x one. And
`/js-only.html` appears in the crawl at all — it is linked from nowhere in any
raw HTML, so day 1 could never have found it.

`html/raw/` and `html/rendered/` now hold gzipped copies of everything, so
day 3 can iterate on the parser without re-crawling a client's site.

## Design notes

- **One browser, N pages.** Launching Chromium costs about a second. Per-URL
  launching would dominate the crawl.
- **Images, fonts and media are aborted at the network layer.** The DOM is the
  product, not the pixels — this roughly triples speed and spares the client
  bandwidth.
- **A render timeout is not a failure.** Whatever rendered is captured and the
  record is flagged `render_timed_out`. One stuck page must not cost the page.
- **Rendering is skipped for non-200s and non-HTML.** No point loading a 404
  in a browser.

## Notes for day 3

- `extract_links()` in `fetcher.py` is still the regex scaffold. It now runs
  against the rendered DOM as well, which is why it survived another day —
  `parse.py` replaces it tomorrow, and it should be deleted then.
- `HtmlStore` is what makes day 3 pleasant: parse from disk, not from the web.
- 39 tests now. `test_htmlstore.py` and `test_extract_links.py` are new.
