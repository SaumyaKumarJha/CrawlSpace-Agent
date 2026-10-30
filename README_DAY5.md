# Crawlspace — Day 5

Hardening. Nothing new to look at in the output; everything here is about a
crawl surviving the real world and being cheap to restart.

## Run

```bash
python -m crawlspace.cli crawl example.com --pages 650 --rps 1

# it died, or you pressed Ctrl-C — pick up where it stopped
python -m crawlspace.cli crawl example.com --pages 650 --rps 1 --resume
```

`--resume` needs the same domain, the same `--out` and the same day, because
the output folder is `audits/<host>/<date>/`.

## What changed

| file | change |
|---|---|
| `crawler.py` | `--resume`: reloads `pages.jsonl`, replays it into the frontier |
| `frontier.py` | marks resumed URLs as already seen |
| `cli.py` | `--resume` flag |
| `intake.py`, `politeness.py`, `sitemaps.py` | survive non-httpx exceptions |
| `crawler.py` | image stats count each image once |
| `pyproject.toml` | `selectolax<1.0` |

## Resume

A dead crawl already kept its pages (streaming writes, see
`README_CRASH_FIXES.md`). Resume makes them useful: the pages already fetched
are loaded back and not fetched again, and the page budget counts them.

Three details that the tests pin down:

- **A half-written last line is skipped, not fatal.** A crash mid-write
  leaves one truncated JSON line. Losing that one page is fine; refusing to
  resume because of it is not.
- **Every resumed URL is marked seen before any link is replayed.** Otherwise
  page A's link to page B re-queues B, even though B is in the file a few
  lines further down.
- **Links a resumed page found but never reached are still queued.** That is
  where the interrupted crawl was heading, so the resumed crawl goes there.

## Exceptions below httpx

The crash fix caught non-httpx exceptions in the fetcher. Host resolution,
robots.txt and sitemap fetching had the same hole: an `h2`, `ssl` or `anyio`
error went straight through. All three now catch broadly and degrade:

- robots.txt that errors or is malformed → fail **open** (crawl allowed)
- robots.txt that returns 401/403 → fail **closed** (crawl refused)
- a sitemap that errors → treated as absent, crawl continues
- a host variant that errors → its error is recorded, the other variants still resolve

## Two fixes from the status review

**Image stats counted two different things.** `unique_images` counted each
image once; `sized` and `over_200kb` counted every appearance on every page.
One run printed `10100/2555 sized`. All three now count unique images. The
same hero image on 300 pages is one image to fix, and the report has to say 1.

**A fresh install broke.** selectolax 1.0 removed the Modest backend that
`parse.py` and `template.py` import from (`selectolax.parser`). An existing
venv was fine; any new machine failed at import. Pinned below 1.0. Moving to
the lexbor backend is the real upgrade path, but it means re-checking every
parser test, so it can wait.

## Real-site fixtures — removed

Day 5 saved real client pages into `tests/fixtures/real/` for week 2 to test
against. They have been deleted, along with the client audit folders and the
client names in code and notes. Client HTML does not belong in this repo.

Before week 2, re-capture the same kind of picklist from a site we own
(`newbi.live`) or have permission to keep: a few pages per template, one
non-200 response, one singleton template.

## Still open

- **Template signature may be too coarse.** On one Wix crawl without
  rendering, one signature covered 275 of 300 pages: home, blog index and
  category pages together. The same site with rendering gave 12 templates.
  Check this before trusting "your X template has issue Y".
- **Unique offsite URLs looked too high** (1,394 on a 593-page site). If
  `same_site()` misclassifies part of the site, ONP-13 inherits the error.
- **Word count** includes boilerplate on platform sites.
- **Checks must refuse site-wide comparisons on a truncated crawl.**
  `site.json` carries `crawl_complete`; enforce it in `checks/base.py`.
- **SSRF protection** before anything public-facing.

## Verify

```bash
pytest tests/ -q                       # 131 tests
```

## Next: week 2

`checks/base.py` first: the check interface, loading a finished crawl from
disk without re-crawling, grouping findings by template, and the
truncated-crawl guard. Then crawlability checks (CRW).
