"""Crawl trap detection.

Some URL spaces are infinite. A calendar will happily serve you the year 3025,
and a shop with five filters generates more permutations than it has products.
Left alone, the crawler spends its whole page budget in one of these and the
audit reports on a corner of the site nobody visits.

Cheap pattern checks, applied before a URL enters the queue.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

# Path fragments that almost always mean an endless space.
TRAP_PATH_PATTERNS = (
    re.compile(r"/(calendar|events?)/\d{4}(/\d{1,2})?(/\d{1,2})?/?$", re.I),
    re.compile(r"/\d{4}/\d{2}/\d{2}/page/\d+", re.I),
    re.compile(r"/page/\d{3,}", re.I),          # page 100+ of anything
    re.compile(r"/(cart|checkout|basket|wishlist)(/|$)", re.I),
    re.compile(r"/(login|signin|signup|register|logout|my-?account)(/|$)", re.I),
    re.compile(r"/wp-admin(/|$)", re.I),
    re.compile(r"/(feed|rss|atom)(/|$)", re.I),
    re.compile(r"/\?", re.I),
)

# Query parameters that fan out combinatorially. Faceted navigation is the
# single most common way a 200-page shop looks like a 200,000-page one.
FACET_PARAMS = {
    "filter", "filters", "facet", "refine", "sort", "sort_by", "orderby",
    "order", "price", "price_min", "price_max", "min_price", "max_price",
    "color", "colour", "size", "brand", "material", "rating", "availability",
    "view", "layout", "per_page", "limit", "offset", "attribute",
}

# Params that are fine on their own — pagination and search are legitimate
# pages worth crawling, up to a point.
BENIGN_PARAMS = {"page", "p", "q", "s", "search", "id", "lang", "variant"}

MAX_FACET_PARAMS = 2      # beyond this it is a filter combination, not a page
MAX_PATH_SEGMENTS = 8


def is_trap(url: str) -> tuple[bool, str]:
    """Returns (is_trap, reason). Reason is empty when the URL is fine."""
    parts = urlsplit(url)
    path = parts.path or "/"

    for pattern in TRAP_PATH_PATTERNS:
        if pattern.search(path):
            return True, f"path matches trap pattern {pattern.pattern}"

    segments = [s for s in path.split("/") if s]
    if len(segments) > MAX_PATH_SEGMENTS:
        return True, f"path has {len(segments)} segments"

    # A segment repeated three times running is a relative-link loop:
    # /shop/shop/shop/product. Real sites do not do this; broken templates do.
    for i in range(len(segments) - 2):
        if segments[i] == segments[i + 1] == segments[i + 2]:
            return True, f"repeated path segment '{segments[i]}'"

    if parts.query:
        keys = [k.lower() for k, _ in parse_qsl(parts.query, keep_blank_values=True)]
        facets = [k for k in keys if k in FACET_PARAMS]
        if len(facets) > MAX_FACET_PARAMS:
            return True, f"{len(facets)} facet parameters ({', '.join(sorted(set(facets)))})"
        unknown = [k for k in keys if k not in FACET_PARAMS and k not in BENIGN_PARAMS]
        if len(unknown) > 4:
            return True, f"{len(unknown)} unrecognised query parameters"

    return False, ""


def parameter_signature(url: str) -> str:
    """The set of parameter names, ignoring values.

    Two URLs with the same signature are the same template with different
    filters applied. Counting distinct signatures against distinct URLs is how
    check CRW-13 spots a parameter explosion.
    """
    parts = urlsplit(url)
    if not parts.query:
        return ""
    keys = sorted({k.lower() for k, _ in parse_qsl(parts.query, keep_blank_values=True)})
    return "&".join(keys)
