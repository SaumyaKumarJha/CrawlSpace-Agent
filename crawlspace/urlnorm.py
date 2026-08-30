"""URL normalisation.

Every dedup decision in the whole audit depends on this module. Two URLs that
are the same page must normalise to the same string, and two URLs that are
different pages must not. Get this wrong and the crawler either misses pages
or crawls the same page forty times.
"""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, urljoin

# Params that never change the content served. Stripping them collapses a huge
# amount of duplicate crawling on any site that has ever run a paid campaign.
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "gclid", "gbraid", "wbraid", "fbclid", "msclkid", "mc_cid",
    "mc_eid", "igshid", "ref", "ref_src", "_ga", "yclid", "dclid",
}

DEFAULT_PORTS = {"http": "80", "https": "443"}

# Extensions we never want in the HTML frontier.
NON_HTML_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".ico",
    ".css", ".js", ".mjs", ".json", ".xml", ".rss", ".atom",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".gz", ".tar", ".7z",
    ".mp4", ".webm", ".mp3", ".wav", ".mov", ".avi",
    ".woff", ".woff2", ".ttf", ".eot",
)


def normalise(url: str, *, base: str | None = None, keep_query: bool = True) -> str | None:
    """Return the canonical string form of a URL, or None if unusable.

    Rules applied, in order:
      - resolve against base if relative
      - lowercase scheme and host, strip default port
      - drop the fragment entirely
      - drop tracking params, sort the rest for stable ordering
      - collapse an empty path to "/"
    """
    if not url:
        return None
    url = url.strip()
    if not url or url.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
        return None

    if base:
        url = urljoin(base, url)

    try:
        parts = urlsplit(url)
    except ValueError:
        return None

    if parts.scheme not in ("http", "https"):
        return None
    if not parts.hostname:
        return None

    host = parts.hostname.lower()
    port = parts.port
    netloc = host
    if port and str(port) != DEFAULT_PORTS.get(parts.scheme, ""):
        netloc = f"{host}:{port}"

    path = parts.path or "/"
    # collapse duplicate slashes but never touch the scheme separator
    while "//" in path:
        path = path.replace("//", "/")

    query = ""
    if keep_query and parts.query:
        pairs = [
            (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS
        ]
        # Sorting makes ?a=1&b=2 and ?b=2&a=1 the same URL. Sites that depend on
        # param order are rare enough to be worth the trade.
        query = urlencode(sorted(pairs), doseq=True)

    return urlunsplit((parts.scheme, netloc, path, query, ""))


def registrable_host(url: str) -> str:
    """Host without a leading www, for same-site comparison."""
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def same_site(url: str, root: str) -> bool:
    """True if url belongs to the site rooted at root, subdomains included."""
    h, r = registrable_host(url), registrable_host(root)
    return bool(h) and (h == r or h.endswith("." + r))


def looks_non_html(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return path.endswith(NON_HTML_SUFFIXES)


def path_depth(url: str) -> int:
    return len([s for s in urlsplit(url).path.split("/") if s])
