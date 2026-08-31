"""Template signature — the DOM-shape hash that groups pages by template.

This is the single highest-leverage idea in the crawler. It turns

    "142 pages are missing meta descriptions"

into

    "your product template is missing a meta description, affecting 142 pages"

The second sentence is worth ten times the first to a client, because it is one
fix rather than 142. It also tells us which ten URLs to send to PageSpeed
instead of three hundred.

The trick is to hash structure while ignoring content. Two product pages differ
entirely in their text and not at all in their shape.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter

from selectolax.parser import HTMLParser, Node

MAX_DEPTH = 4          # deeper than this is content, not layout
SIGNATURE_LENGTH = 8

# Class names that encode content, state, or a build hash rather than
# structure. Including them splits one template into dozens of signatures.
_STATE_WORDS = (
    "active", "current", "selected", "open", "closed", "hover", "focus",
    "hidden", "show", "visible", "expanded", "collapsed", "loading", "error",
    "disabled", "checked", "sticky", "scrolled",
)


def _looks_generated(token: str) -> bool:
    """True for build-tool output: css-1a2b3c4, sc-bdVaJa, jsx-3847592."""
    for part in re.split(r"[-_]", token):
        if len(part) < 5:
            continue
        digits = sum(c.isdigit() for c in part)
        if digits >= 2:
            return True
        # styled-components style: mixed case inside one segment
        if any(c.islower() for c in part) and any(c.isupper() for c in part):
            return True
    return bool(re.search(r"\d{3,}", token))


def _is_volatile(token: str) -> bool:
    low = token.lower()
    if len(low) <= 2:
        return True
    if low.startswith(("is-", "has-", "js-")):
        return True
    if any(word in low for word in _STATE_WORDS):
        return True
    return _looks_generated(token)


# Tags that carry no layout meaning.
_IGNORED_TAGS = {"script", "style", "noscript", "template", "br", "-undef", "_comment"}


def _stable_classes(node: Node) -> list[str]:
    raw = node.attributes.get("class") or ""
    keep = [c for c in raw.split() if c and not _is_volatile(c)]
    return sorted(keep)[:3]      # three is enough to distinguish; more is noise


def _walk(node: Node, depth: int, out: list[str]) -> None:
    if depth > MAX_DEPTH:
        return
    for child in node.iter(include_text=False):
        tag = child.tag
        if tag in _IGNORED_TAGS:
            continue
        classes = _stable_classes(child)
        token = tag if not classes else f"{tag}.{'.'.join(classes)}"
        out.append(f"{depth}:{token}")
        _walk(child, depth + 1, out)


def signature(html: str) -> str | None:
    """A short stable hash of the page's structure, or None if unparseable."""
    if not html:
        return None

    tree = HTMLParser(html)
    body = tree.body
    if body is None:
        return None

    tokens: list[str] = []
    _walk(body, 0, tokens)
    if not tokens:
        return None

    # Counting tokens rather than keeping their order means a listing page with
    # 12 cards and one with 8 still match. Order-sensitivity would split every
    # category page by how many products it happens to show.
    counts = Counter(tokens)
    shape = "|".join(f"{t}x{_bucket(n)}" for t, n in sorted(counts.items()))
    return hashlib.sha1(shape.encode("utf-8")).hexdigest()[:SIGNATURE_LENGTH]


def _bucket(n: int) -> str:
    """Bucket repeat counts so 8 cards and 12 cards land in the same template."""
    if n <= 2:
        return str(n)
    if n <= 5:
        return "3-5"
    if n <= 12:
        return "6-12"
    if n <= 30:
        return "13-30"
    return "30+"


def group_by_template(records) -> dict[str, list]:
    """Bucket PageRecords by their signature. Unsignatured pages group alone."""
    groups: dict[str, list] = {}
    for record in records:
        key = record.template_signature or f"_none:{record.url}"
        groups.setdefault(key, []).append(record)
    return groups


def representative_urls(records, *, limit: int = 12) -> list[str]:
    """One URL per template, shallowest first, capped.

    This is the list that goes to PageSpeed Insights on day 11 — testing every
    page would burn the quota and tell us the same thing twelve times.
    """
    groups = group_by_template([r for r in records if r.ok and r.is_html])
    picks: list = []
    for members in groups.values():
        picks.append(min(members, key=lambda r: (r.depth, r.url)))
    picks.sort(key=lambda r: (r.depth, r.url))
    return [r.final_url or r.url for r in picks[:limit]]
