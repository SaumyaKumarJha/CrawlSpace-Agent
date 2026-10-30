# Crawlspace — surviving the first big client crawl crash

A 569-page crawl died at page ~450 with:

```
h2.exceptions.InvalidBodyLengthError: Expected 0 bytes, received 44
```

Ten minutes of polite crawling, discarded. Three separate faults, all fixed.

## 1. HTTP/2 is now off by default

**What happened.** The server sent a frame the `h2` library considers
malformed. HTTP/1.1 tolerates it; HTTP/2 refuses and raises.

**Why it was on.** No good reason. HTTP/2's advantage is multiplexing many
requests down one connection — irrelevant when we deliberately crawl at one
request per second.

**Fixed.** `http2=False` by default. `--http2` opts back in if a site ever
needs it. Nothing is lost: HTTP/1.1 fetches identical HTML.

## 2. The exception escaped every handler

`fetcher.py` caught `httpx.HTTPError`. `InvalidBodyLengthError` is an `h2`
exception, not an httpx one, so it sailed straight through and up out of
`asyncio.gather`, taking every worker with it.

**Fixed, in two layers:**

- The request and the body read now catch `Exception`. Deliberately broad:
  httpx wraps most transport failures, but the libraries beneath it — `h2`,
  `ssl`, `anyio` — raise their own types straight through.
- Each page is processed inside its own try/except in the worker loop. A page
  that fails in fetching, rendering, parsing or storage costs that page and
  is recorded with the error. Only `CrawlBlocked` still stops the run, because
  that one means the whole crawl is invalid.

**The principle:** one bad page must never cost the other 568.

## 3. A dead crawl lost everything

The real damage. `pages.jsonl` was written once, at the very end, so a crash
at page 450 threw away all 450 — as did every Ctrl-C.

**Fixed.** Records now stream to disk as they are fetched, flushed per page.
A crawl that dies keeps everything it had. Ctrl-C exits cleanly, says where
the partial file is, and returns 130.

Verified: a crawl killed mid-run leaves its fetched pages on disk.

## Also: "slow to settle" was meaningless

The checkpoint showed **410 of 410 pages** flagged. On Wix, network-idle never
arrives — there is always an analytics or chat socket open — so the flag was
true for every page on every platform site.

A metric that is always true is worse than no metric. It now reports a rate,
and above 90% says what it actually means:

```
  network never idle on 100% of pages — normal for this platform,
  DOM captured regardless
```

The underlying two-stage wait was working correctly; only the reporting was
misleading.

## What the failed run still told us

Worth keeping, from `state.json` at the moment it died:

| signal | value | reading |
|---|---|---|
| canonical host | `https://www.example.com/` | apex redirects to www correctly |
| offsite unique | **1021** | needs investigating — high for a 569-page site |
| js_dependent | 0 | Wix server-renders; content is in the raw HTML |
| skipped_traps | 0 | no faceted-filter explosion on this site |

The 1021 unique external URLs is the open question. If `same_site()` is
misclassifying a subdomain, the internal link graph is wrong and check ONP-13
would inherit that error.

## Re-run

```bash
python -m crawlspace.cli crawl example.com --pages 650 --rps 1
```

If it dies again, `pages.jsonl` keeps whatever it reached.

114 tests pass.
