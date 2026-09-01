"""Day 1 CLI. Grows into the full audit command by day 19."""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from .crawler import Crawler, CrawlBlocked
from .models import CrawlConfig
from .urlnorm import normalise


def _progress(done: int, stats: dict) -> None:
    sys.stderr.write(
        f"\r  fetched {done:>4}  queued {stats['queued']:>4}  seen {stats['seen']:>4}  "
    )
    sys.stderr.flush()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="crawlspace")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("crawl", help="fetch pages and write pages.jsonl")
    c.add_argument("domain")
    c.add_argument("--pages", type=int, default=100)
    c.add_argument("--depth", type=int, default=5)
    c.add_argument("--rps", type=float, default=1.5)
    c.add_argument("--concurrency", type=int, default=3)
    c.add_argument("--ignore-robots", action="store_true",
                   help="only for sites you own — never for a prospect")
    c.add_argument("--no-render", action="store_true",
                   help="skip Playwright; faster, but misses JS-built navigation")
    c.add_argument("--render-timeout", type=float, default=15.0)
    c.add_argument("--settle", type=int, default=1500,
                   help="ms to wait for network-idle after DOM ready")
    c.add_argument("--no-images", action="store_true",
                   help="skip image size lookups")
    c.add_argument("--http2", action="store_true",
                   help="enable HTTP/2 (off by default — some servers send "
                        "frames the h2 library refuses, killing the crawl)")
    c.add_argument("--out", default="audits")
    c.add_argument("--resume", action="store_true",
                   help="pick up an interrupted crawl instead of restarting — "
                        "same domain, same --out, same day; already-fetched "
                        "pages are kept and not re-fetched")

    args = ap.parse_args(argv)

    domain = args.domain
    if not domain.startswith(("http://", "https://")):
        domain = "https://" + domain
    root = normalise(domain)
    if not root:
        print(f"Could not parse domain: {args.domain}", file=sys.stderr)
        return 2

    host = root.split("//", 1)[1].split("/")[0]
    out_dir = Path(args.out) / host / date.today().isoformat()

    config = CrawlConfig(
        domain=root,
        max_pages=args.pages,
        max_depth=args.depth,
        requests_per_second=args.rps,
        concurrency=args.concurrency,
        obey_robots=not args.ignore_robots,
        render_timeout_s=args.render_timeout,
        render_settle_ms=args.settle,
        size_images=not args.no_images,
        http2=args.http2,
    )

    mode = "fetch only" if args.no_render else "fetch + render"
    print(f"Crawling {root}  (cap {args.pages} pages, depth {args.depth}, "
          f"{args.rps} req/s, {mode})")
    crawler = Crawler(config, out_dir, render=not args.no_render, resume=args.resume)
    if crawler.resumed:
        print(f"  resuming — {len(crawler.records)} pages already fetched, "
              f"kept from {out_dir / 'pages.jsonl'}")

    try:
        records = asyncio.run(crawler.run(on_progress=_progress))
    except CrawlBlocked as exc:
        sys.stderr.write("\n")
        print(f"CRAWL ABORTED — {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        sys.stderr.write("\n")
        print("Interrupted. Pages fetched so far are saved in "
              f"{out_dir / 'pages.jsonl'}", file=sys.stderr)
        return 130

    sys.stderr.write("\n")
    statuses = Counter(r.status for r in records)
    errors = [r for r in records if r.fetch_error]
    html = [r for r in records if r.is_html]
    redirected = [r for r in records if r.redirect_chain]

    print(f"\n  pages fetched     {len(records)}")
    print(f"  html pages        {len(html)}")
    print(f"  redirects seen    {len(redirected)}")
    print(f"  fetch errors      {len(errors)}")
    print("  status codes      " + ", ".join(
        f"{k}: {v}" for k, v in sorted(statuses.items(), key=lambda x: (x[0] is None, x[0]))
    ))
    print("  frontier          " + ", ".join(f"{k}={v}" for k, v in crawler.frontier.stats().items()))

    sm = crawler.sitemap
    if sm:
        print(f"\n  sitemaps read     {len(sm.sources)}")
        print(f"  sitemap urls      {len(sm.urls)}")
        if not sm.exists:
            print("  sitemap           NONE FOUND")
        elif not sm.referenced_in_robots:
            print("  sitemap           found, but not declared in robots.txt")

    host = crawler.host
    if host and host.notes:
        for note in host.notes:
            print(f"  host              {note}")

    orphans = crawler.orphans()
    tpl = crawler.templates()
    if crawler.crawl_complete:
        print(f"\n  orphan pages      {len(orphans)}  (in sitemap, no internal links)")
    else:
        remaining = crawler.frontier.stats()["queued"]
        print(f"\n  orphan pages      not measured — crawl hit the page cap "
              f"with {remaining} URLs still queued.")
        print(f"                    re-run with --pages {len(crawler.records) + remaining + 50} "
              f"for a complete crawl.")
    print(f"  templates         {tpl['distinct']} distinct across "
          f"{tpl['pages_with_signature']} pages")
    if crawler.frontier.trap_reasons:
        top = sorted(crawler.frontier.trap_reasons.items(),
                     key=lambda kv: -kv[1])[:3]
        print("  traps skipped     " + "; ".join(f"{n}x {r}" for r, n in top))

    img = crawler.image_stats()
    if img["sized"]:
        print(f"  images            {img['sized']}/{img['unique_images']} sized, "
              f"{img['over_200kb']} over 200KB, "
              f"largest {img['largest_bytes'] // 1024}KB")

    if not args.no_render:
        rs = crawler.stats()
        print(f"\n  rendered          {rs['rendered']}")
        if rs["never_idle_pct"] >= 90:
            print(f"  network never idle on {rs['never_idle_pct']}% of pages — "
                  f"normal for this platform, DOM captured regardless")
        elif rs["render_timeouts"]:
            print(f"  slow to settle    {rs['render_timeouts']}")
        print(f"  render failures   {rs['render_failures']}")
        print(f"  JS-dependent      {rs['js_dependent']}  "
              f"(rendered DOM >3x raw HTML — preview of check ONP-10)")
    print(f"\n  wrote {out_dir / 'pages.jsonl'}")
    print(f"  wrote {out_dir / 'site.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
