# Crawlspace — Day 4

Sitemaps, host resolution, crawl traps, orphan detection and the template
signature. Week 1 is done: the crawler is finished.

## Run

```bash
python -m crawlspace.cli crawl newbi.live --pages 50
```

New output file: `site.json` — everything true about the site as a whole
rather than about one page.

```
  sitemaps read     1
  sitemap urls      248
  sitemap           found, but not declared in robots.txt

  orphan pages      12  (in sitemap, no internal links)
  templates         9 distinct across 187 pages
  traps skipped     41x 4 facet parameters (brand, color, size, sort)
```

## What changed

| file | change |
|---|---|
| `intake.py` | **new** — probes all four host variants, picks the canonical one |
| `sitemaps.py` | **new** — discovery, index recursion, gzip, malformed XML |
| `traps.py` | **new** — infinite URL space detection |
| `template.py` | **new** — the DOM-shape hash |
| `frontier.py` | trap filtering, orphan detection, discovery sources |
| `crawler.py` | host resolution first, sitemap seeding, `site.json` |

## The four ideas

**Host resolution runs before anything else.** Every site has four addresses —
`https://`, `https://www.`, `http://`, `http://www.` — and only one is real.
Crawl the wrong one and every internal link looks like a cross-host redirect;
the redirect check then reports hundreds of findings that do not exist. It is
also a check in its own right: if more than one variant answers 200 without
redirecting, the site is serving the same content at up to four addresses.

**Sitemaps seed the frontier.** Not just for coverage — because a URL found
*only* in the sitemap and never via a link is an orphan. The frontier records
how each URL was discovered, which is the only reason that check is possible.

**Traps are refused before they enter the queue.** A calendar will serve you
the year 3025. A shop with five filters generates more permutations than it has
products. Without this, a 300-page budget disappears into one corner of the
site and the audit reports on pages nobody visits.

**The template signature is the one that makes money.** Hash the page's shape
while ignoring its content, and two product pages come out identical. That
turns

> 142 pages are missing meta descriptions

into

> your product template is missing a meta description, affecting 142 pages

One fix instead of 142. It also picks the twelve URLs to send to PageSpeed on
day 11 instead of three hundred.

## Verify

```bash
pytest tests/ -q                       # 110 tests
python tests/fixtures/serve.py 8899    # terminal 1
python -m crawlspace.cli crawl http://127.0.0.1:8899/ --pages 30 --rps 20
```

Verified on the fixture site:

| signal | result |
|---|---|
| sitemap | 1 read, 3 URLs, declared in robots.txt |
| orphan pages | **1** — `/orphan.html`, discovered via `sitemap` only |
| traps skipped | **4** — the 4-facet shop URL and the 2031 calendar |
| templates | 7 distinct across 10 pages |

## Two bugs found while building this

**`host_variants` dropped the port and forced https.** `http://127.0.0.1:8899/`
became `https://127.0.0.1/`, and the whole crawl died at the first request.
Fixed: an explicit non-default port or a literal host (an IP, or a name with no
dots) gets one variant, not four. Worth knowing because it is the same reason
you cannot run this against a staging server on a port without the fix.

**Generated class names split one template into dozens.** `css-1a2b3c4` and
`sc-bdVaJa` are build-tool output and differ per element, so including them in
the signature made every page unique. `_is_volatile()` now drops generated
names, state classes (`is-active`, `has-error`) and single letters, and keeps
the structural ones (`card`, `product`, `nav-item`).

## Week 1 complete

The crawler does everything it will ever do: fetch, render, parse, group, and
report on the site as a whole. Days 5 onwards are hardening and then checks.

Next: **day 5** — checkpoint resumability, the block-abort guard under real
conditions, and saving fixtures from real client sites. Then week 2, where
this stops being a crawler and becomes an audit.
