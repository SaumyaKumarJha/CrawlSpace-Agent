# Crawlspace — Day 1

The fetch layer. Crawls a domain politely, records what came back, and writes
`pages.jsonl`. No rendering (day 2), no parsing (day 3), no checks (week 2).

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev]"
```

## Run

```bash
python -m crawlspace.cli crawl newbi.live --pages 50
python -m crawlspace.cli crawl example.com --pages 100 --rps 1
```

Output lands in `audits/<host>/<date>/`:

| file | what it is |
|---|---|
| `pages.jsonl` | one PageRecord per URL |
| `state.json` | checkpoint, rewritten every 10 pages |
| `sitemaps.txt` | sitemaps declared in robots.txt (day 4 parses them) |

## Test

```bash
pytest tests/ -q                      # 29 unit tests, no network

# end-to-end against the committed fixture site
python tests/fixtures/serve.py 8899   # in one terminal
python -m crawlspace.cli crawl http://127.0.0.1:8899/ --pages 25 --rps 20
```

Expected fixture result: 8 pages seen, 6× 200, 1× 404, 1 redirect,
1 blocked by robots, 2 off-site links skipped, 2 non-HTML skipped.

## What Day 1 gives you

| module | responsibility |
|---|---|
| `models.py` | `PageRecord`, `Finding`, `CrawlConfig` — the contract everything else codes against |
| `urlnorm.py` | URL normalisation, scope checks, non-HTML filtering |
| `politeness.py` | robots.txt gate + global rate limiter |
| `frontier.py` | breadth-first queue, seen-set, depth and page caps |
| `fetcher.py` | httpx GET with retries, redirect chains, header capture |
| `crawler.py` | worker pool, checkpointing, block-abort guard |
| `cli.py` | `crawlspace crawl` |

## Day 1 acceptance test

> Fetch 50 URLs from newbi.live at a polite rate, with correct depth values
> and zero off-host requests.

Check `frontier` line in the summary: `skipped_offsite` should be non-zero on
any real site and no record's URL should be on another host.

## Notes for day 2

- `fetcher._extract_links` is a regex scaffold so the frontier can be fed today.
  `parse.py` replaces it with selectolax extraction on day 3. Delete the regex then.
- `PageRecord.rendered_html_len` and `render_timed_out` are already in the model
  but always 0/False until Playwright lands tomorrow.
- The fixture site is deliberately small and static. Add a saved copy of a real
  Wix page to `tests/fixtures/` on day 5.
