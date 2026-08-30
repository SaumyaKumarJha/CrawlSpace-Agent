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
    c.add_argument("--out", default="audits")

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
    )

    print(f"Crawling {root}  (cap {args.pages} pages, depth {args.depth}, {args.rps} req/s)")
    crawler = Crawler(config, out_dir)

    try:
        records = asyncio.run(crawler.run(on_progress=_progress))
    except CrawlBlocked as exc:
        sys.stderr.write("\n")
        print(f"CRAWL ABORTED — {exc}", file=sys.stderr)
        return 3

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
    print(f"\n  wrote {out_dir / 'pages.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
