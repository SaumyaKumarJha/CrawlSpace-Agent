"""Sitemap discovery and parsing.

A sitemap is the client telling Google which pages matter. Comparing it against
what the crawl actually found is where several findings come from: URLs in the
sitemap that 404, URLs that canonicalise elsewhere, and — the valuable one —
pages listed in the sitemap that nothing on the site links to.
"""
from __future__ import annotations

import gzip
import re
from dataclasses import dataclass, field
from xml.etree import ElementTree

import httpx

from .urlnorm import normalise

# Where sitemaps live when robots.txt does not say. Ordered by how often they
# are right in practice.
COMMON_SITEMAP_PATHS = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/sitemap.xml.gz",
    "/wp-sitemap.xml",          # WordPress core
    "/sitemap_index.xml.gz",
    "/sitemaps/sitemap.xml",
)

# Guards against a malicious or broken sitemap index pointing at itself.
MAX_SITEMAP_DEPTH = 3
MAX_SITEMAP_URLS = 50_000

_NS = re.compile(r"^\{[^}]+\}")


@dataclass(slots=True)
class SitemapReport:
    urls: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)      # sitemaps actually read
    declared_in_robots: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    found_at_common_path: bool = False

    @property
    def exists(self) -> bool:
        return bool(self.sources)

    @property
    def referenced_in_robots(self) -> bool:
        return bool(self.declared_in_robots)


def _strip_ns(tag: str) -> str:
    return _NS.sub("", tag)


def parse_sitemap_xml(content: bytes) -> tuple[list[str], list[str]]:
    """Returns (page urls, nested sitemap urls).

    Handles both document types — a urlset of pages and a sitemapindex of
    further sitemaps — because a caller cannot know which it fetched until it
    looks, and large sites always use the second.
    """
    if content[:2] == b"\x1f\x8b":          # gzip magic number
        try:
            content = gzip.decompress(content)
        except OSError:
            return [], []

    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return [], []

    kind = _strip_ns(root.tag)
    pages: list[str] = []
    nested: list[str] = []

    for child in root:
        if _strip_ns(child.tag) not in ("url", "sitemap"):
            continue
        loc = None
        for node in child:
            if _strip_ns(node.tag) == "loc" and node.text:
                loc = node.text.strip()
                break
        if not loc:
            continue
        (nested if kind == "sitemapindex" else pages).append(loc)

    return pages, nested


async def collect_sitemaps(client: httpx.AsyncClient, root: str,
                           declared: list[str]) -> SitemapReport:
    """Discover, fetch and flatten every sitemap for a site."""
    report = SitemapReport(declared_in_robots=list(declared))

    queue: list[tuple[str, int]] = [(u, 0) for u in declared]

    if not queue:
        # Nothing declared in robots.txt — probe the usual locations. Finding
        # one here is itself a finding: the sitemap exists but is undeclared.
        for path in COMMON_SITEMAP_PATHS:
            candidate = root.rstrip("/") + path
            if await _exists(client, candidate):
                queue.append((candidate, 0))
                report.found_at_common_path = True
                break

    seen: set[str] = set()

    while queue:
        url, depth = queue.pop(0)
        if url in seen or depth > MAX_SITEMAP_DEPTH:
            continue
        seen.add(url)

        try:
            response = await client.get(url, timeout=20.0)
        except httpx.HTTPError as exc:
            report.errors.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
            continue

        if response.status_code != 200:
            report.errors.append({"url": url, "error": f"HTTP {response.status_code}"})
            continue

        pages, nested = parse_sitemap_xml(response.content)
        if not pages and not nested:
            report.errors.append({"url": url, "error": "unparseable or empty"})
            continue

        report.sources.append(url)

        for loc in pages:
            clean = normalise(loc)
            if clean and len(report.urls) < MAX_SITEMAP_URLS:
                report.urls.append(clean)

        for loc in nested:
            clean = normalise(loc)
            if clean:
                queue.append((clean, depth + 1))

    return report


async def _exists(client: httpx.AsyncClient, url: str) -> bool:
    try:
        response = await client.head(url, timeout=10.0)
        if response.status_code == 405:      # some hosts refuse HEAD
            response = await client.get(url, timeout=10.0)
        return response.status_code == 200
    except httpx.HTTPError:
        return False
