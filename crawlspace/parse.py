"""HTML into PageRecord fields.

Everything downstream trusts this file. If it reads a canonical wrongly, four
separate checks lie in unison and a client's developer finds it before we do —
so this module is deliberately conservative: it records what is there, makes no
inferences, and distinguishes "absent" from "present but empty" everywhere that
distinction carries meaning.

Pure by design. No network, no side effects. It takes a string of HTML and
returns data, which is what makes it testable against saved fixtures.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from .models import ImageRef, LinkRef
from .textwidth import pixel_width
from .urlnorm import normalise, same_site

# Stripped before counting words. These carry no content a reader would call
# the page's content, and leaving them in makes every page look substantial.
BOILERPLATE_TAGS = (
    "script", "style", "noscript", "svg", "template", "iframe",
    "nav", "header", "footer", "aside", "form", "button", "select",
)

# Preferred content roots, in order. Falling back to <body> is normal.
CONTENT_SELECTORS = ("main", "article", '[role="main"]', "#content", "#main")

# Word characters including non-Latin scripts, so Hindi or Tamil pages are not
# reported as having zero words.
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_NOFOLLOW_RELS = {"nofollow", "ugc", "sponsored"}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _attr(node: Node | None, name: str) -> str | None:
    """None means the attribute is absent. '' means present and empty.

    selectolax stores a valueless or empty attribute as None, which would
    erase exactly that distinction — so presence is tested against the keys,
    not the value. This matters: alt="" is a correct, deliberate marker for a
    decorative image, and reporting it as missing alt text is a false positive
    on every well-built site we audit.
    """
    if node is None or name not in node.attributes:
        return None
    return node.attributes[name] or ""


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _meta_content(tree: HTMLParser, name: str) -> str | None:
    for node in tree.css("meta"):
        value = node.attributes.get("name")
        if value and value.lower() == name:
            return node.attributes.get("content")
    return None


# --------------------------------------------------------------------------
# field extractors
# --------------------------------------------------------------------------

def extract_title(tree: HTMLParser) -> dict[str, Any] | None:
    node = tree.css_first("title")
    if node is None:
        return None
    text = _clean(node.text())
    return {"text": text, "len": len(text), "px_width": pixel_width(text)}


def extract_meta_description(tree: HTMLParser) -> dict[str, Any] | None:
    content = _meta_content(tree, "description")
    if content is None:
        return None
    text = _clean(content)
    return {"text": text, "len": len(text)}


def extract_canonical(tree: HTMLParser, page_url: str) -> str | None:
    for node in tree.css('link[rel="canonical"]'):
        href = node.attributes.get("href")
        if href:
            return normalise(href, base=page_url)
    return None


def extract_robots_meta(tree: HTMLParser) -> str | None:
    """Merged robots directives, generic plus googlebot-specific."""
    generic = _meta_content(tree, "robots")
    googlebot = _meta_content(tree, "googlebot")
    parts = [p for p in (generic, googlebot) if p]
    if not parts:
        return None
    return ",".join(_clean(p).lower() for p in parts)


def extract_headings(tree: HTMLParser) -> tuple[list[str], list[tuple[str, int]]]:
    """Returns (h1 texts, full outline as (tag, level) in document order)."""
    h1s: list[str] = []
    outline: list[tuple[str, int]] = []
    for node in tree.css("h1, h2, h3, h4, h5, h6"):
        tag = node.tag
        level = int(tag[1])
        outline.append((tag, level))
        if level == 1:
            h1s.append(_clean(node.text()))
    return h1s, outline


def main_content_node(tree: HTMLParser) -> Node | None:
    for selector in CONTENT_SELECTORS:
        node = tree.css_first(selector)
        if node is not None:
            return node
    return tree.body


def extract_word_count(tree: HTMLParser) -> int:
    """Words in the main content, boilerplate removed.

    Counted on a copy: stripping nodes mutates the tree, and every extractor
    after this one would then be reading a page with its nav deleted.
    """
    copy = HTMLParser(tree.html or "")
    copy.strip_tags(list(BOILERPLATE_TAGS))
    node = main_content_node(copy)
    if node is None:
        return 0
    return len(_WORD_RE.findall(node.text(separator=" ")))


def extract_links(tree: HTMLParser, page_url: str, root: str
                  ) -> tuple[list[LinkRef], list[LinkRef]]:
    internal: list[LinkRef] = []
    external: list[LinkRef] = []
    for node in tree.css("a[href]"):
        raw = node.attributes.get("href") or ""
        resolved = normalise(raw, base=page_url)
        if not resolved:
            continue
        rels = {r.lower() for r in (node.attributes.get("rel") or "").split()}
        ref = LinkRef(
            href=resolved,
            anchor=_clean(node.text())[:200],
            nofollow=bool(rels & _NOFOLLOW_RELS),
            internal=same_site(resolved, root),
        )
        (internal if ref.internal else external).append(ref)
    return internal, external


def extract_images(tree: HTMLParser, page_url: str) -> list[ImageRef]:
    """alt=None means the attribute is missing. alt='' means explicitly
    decorative, which is correct and must not be reported as a failure."""
    out: list[ImageRef] = []
    for node in tree.css("img"):
        src = node.attributes.get("src") or node.attributes.get("data-src")
        if not src:
            # srcset-only images are legitimate; take the first candidate.
            srcset = node.attributes.get("srcset") or ""
            src = srcset.split(",")[0].strip().split(" ")[0] if srcset else None
        if not src:
            continue
        resolved = normalise(src, base=page_url) or src
        out.append(ImageRef(
            src=resolved,
            alt=_attr(node, "alt"),
            lazy=(node.attributes.get("loading") or "").lower() == "lazy",
        ))
    return out


def extract_hreflang(tree: HTMLParser, page_url: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for node in tree.css('link[rel="alternate"]'):
        lang = node.attributes.get("hreflang")
        href = node.attributes.get("href")
        if lang and href:
            out.append({"lang": lang.lower(),
                        "href": normalise(href, base=page_url) or href})
    return out


def extract_og(tree: HTMLParser) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in tree.css("meta"):
        key = node.attributes.get("property") or node.attributes.get("name") or ""
        key = key.lower()
        if key.startswith(("og:", "twitter:")):
            content = node.attributes.get("content")
            if content:
                out[key] = _clean(content)
    return out


# --------------------------------------------------------------------------
# structured data
# --------------------------------------------------------------------------

def extract_json_ld(tree: HTMLParser) -> list[dict[str, Any]]:
    """JSON-LD blocks, flattened.

    Handles the three real-world shapes: a single object, an array of objects,
    and an @graph wrapper. Invalid JSON is recorded rather than dropped — a
    malformed block is check SDA-04, so silently skipping it would hide the
    very thing we are looking for.
    """
    out: list[dict[str, Any]] = []
    for node in tree.css('script[type="application/ld+json"]'):
        raw = node.text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            out.append({"_format": "json-ld", "_invalid": True,
                        "_error": str(exc)[:200]})
            continue
        for item in _flatten_json_ld(data):
            item["_format"] = "json-ld"
            out.append(item)
    return out


def _flatten_json_ld(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for item in data for x in _flatten_json_ld(item)]
    if not isinstance(data, dict):
        return []
    if "@graph" in data and isinstance(data["@graph"], list):
        return [x for item in data["@graph"] for x in _flatten_json_ld(item)]
    return [dict(data)]


def extract_microdata(tree: HTMLParser) -> list[dict[str, Any]]:
    """Top-level itemscope elements with their direct itemprops.

    Deliberately shallow: nested microdata is rare enough that full recursion
    is not worth the complexity, and the checks only need type plus properties.
    """
    out: list[dict[str, Any]] = []
    for scope in tree.css("[itemscope]"):
        itemtype = scope.attributes.get("itemtype")
        if not itemtype:
            continue
        props: dict[str, Any] = {}
        for prop in scope.css("[itemprop]"):
            name = prop.attributes.get("itemprop")
            if not name:
                continue
            value = (
                prop.attributes.get("content")
                or prop.attributes.get("href")
                or prop.attributes.get("src")
                or _clean(prop.text())
            )
            if name in props:
                existing = props[name]
                props[name] = (existing + [value] if isinstance(existing, list)
                               else [existing, value])
            else:
                props[name] = value
        out.append({
            "_format": "microdata",
            "@type": itemtype.rstrip("/").rsplit("/", 1)[-1],
            "@raw_type": itemtype,
            **props,
        })
    return out


def extract_structured_data(tree: HTMLParser) -> list[dict[str, Any]]:
    return extract_json_ld(tree) + extract_microdata(tree)


# --------------------------------------------------------------------------
# the entry point
# --------------------------------------------------------------------------

def parse_into(record, html: str, root: str) -> None:
    """Fill a PageRecord's parsed fields in place.

    Uses record.final_url as the base for resolving relative URLs — resolving
    against the requested URL after a redirect is a classic source of phantom
    broken links.
    """
    if not html:
        return

    tree = HTMLParser(html)
    base = record.final_url or record.url

    record.title = extract_title(tree)
    record.meta_description = extract_meta_description(tree)
    record.canonical = extract_canonical(tree, base)
    record.robots_meta = extract_robots_meta(tree)
    record.h1, record.heading_outline = extract_headings(tree)
    record.word_count = extract_word_count(tree)
    record.internal_links, record.external_links = extract_links(tree, base, root)
    record.images = extract_images(tree, base)
    record.hreflang = extract_hreflang(tree, base)
    record.og = extract_og(tree)
    record.structured_data = extract_structured_data(tree)


def links_for_frontier(html: str | bytes, page_url: str = "") -> list[str]:
    """Hrefs for the crawl queue. Replaces the day-1 regex scaffold.

    Normally returns hrefs untouched and lets the frontier resolve them. The
    exception is a <base href> element, which changes what every relative link
    on the page points at — ignoring one silently sends the crawler to URLs
    that do not exist.
    """
    if not html:
        return []
    if isinstance(html, bytes):
        html = html.decode("utf-8", "replace")

    tree = HTMLParser(html)

    base = None
    base_node = tree.css_first("base[href]")
    if base_node is not None:
        candidate = base_node.attributes.get("href")
        if candidate:
            base = urljoin(page_url, candidate) if page_url else candidate

    out: list[str] = []
    for node in tree.css("a[href]"):
        href = node.attributes.get("href")
        if not href:
            continue
        out.append(urljoin(base, href) if base else href)
    return out
