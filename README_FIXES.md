# Crawlspace — fixes from the first real client run

Running against azayaorganic.com found four things. Three were bugs; one was
me misreading a number.

## 1. Orphan count was wrong on a truncated crawl — FIXED

**The symptom.** 74 orphans reported on Azaya. Almost none were real.

**The cause.** Sitemap URLs seed the frontier at depth 1. With a 100-page cap
on a 554-page site, the crawler spent nearly its whole budget on sitemap
entries before it had followed enough internal links to discover them the
normal way. Every one of them then looked orphaned.

**The fix.** `Frontier.complete` is true only when the queue emptied *and* the
page cap was not hit. `orphans()` returns nothing otherwise, and the CLI says
so plainly instead of printing a number:

```
  orphan pages      not measured — crawl hit the page cap with 491 URLs
                    still queued.
                    re-run with --pages 645 for a complete crawl.
```

A confidently wrong number is worse than an absent one, because you would have
put it in a client email. `site.json` also carries `orphans_valid`.

## 2. A third of pages "timed out" rendering — FIXED

**The symptom.** 32 render timeouts out of 100 on a site with nothing wrong.

**The cause.** Waiting for `networkidle` sounds right and is wrong on real
sites. Wix, like most platforms, holds a socket open for analytics or chat, so
network-idle never arrives and every page burns the full 15 seconds.

**The fix.** Two-stage wait: `domcontentloaded` first, which always fires, then
a short settle budget (1.5s, `--settle`) for network-idle that we shrug off if
it does not come. Crawls get dramatically faster on platform sites, and the
flag now means the page was genuinely still working rather than merely holding
a socket. Renamed in the output to "slow to settle", because that is what it
measures.

## 3. Image sizes were never fetched — FIXED

`ImageRef.bytes` was `None` everywhere, so check PRF-04 (oversized images)
had nothing to run on.

Now sized in one batch after the crawl, one HEAD per **unique** image URL —
the same hero image appears on every page and sizing it 300 times would be
slow and rude. Capped at 400 lookups; `--no-images` skips it.

```
  images            212/240 sized, 31 over 200KB, largest 1840KB
```

## 4. `skipped_offsite: 437` was not a bug

I flagged this as suspicious. It is not: the counter counts link *instances*,
and a footer with four social links across 100 pages is 400 of them on its own.

Not a bug, but the number was uninterpretable, so `stats()` now reports
`offsite_unique` alongside it. 437 instances / 5 unique URLs reads correctly;
437 / 300 would be the real anomaly.

## Still open

- **SSRF protection** — needed before any public form, not before then.
- **Template signature unvalidated at scale.** 5 templates across 100 Azaya
  pages looks right but has not been checked by eye against the actual pages.
- **Word count on platform sites** leaks boilerplate where semantic tags are
  not used. Will need tuning when the thin-content check lands.
- **The crawl cap distorts every count.** Partly addressed — `complete` is now
  in `site.json` and the checks should refuse to run site-wide comparisons on
  a truncated crawl. Enforced at check level in week 2.

## Re-run Azaya properly

```bash
python -m crawlspace.cli crawl azayaorganic.com --pages 650 --rps 1
```

Complete crawl, so the orphan number becomes real, and the template map covers
all 554 pages rather than a sample. Expect roughly 15 minutes at 1 req/s.

114 tests pass.
