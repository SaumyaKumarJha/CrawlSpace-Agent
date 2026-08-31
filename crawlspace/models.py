"""Core data contracts. Everything else in Crawlspace codes against these.

Written on day 0 deliberately: the crawler is written to fill a known shape,
rather than the shape being reverse-engineered from whatever the crawler
happened to produce.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Literal

Severity = Literal["critical", "high", "medium", "low"]
Pillar = Literal["crawlability", "on_page", "performance", "structured_data", "hygiene"]
Effort = Literal["template", "site", "per_page", "dev"]
DiscoverySource = Literal["seed", "sitemap", "internal_link", "redirect"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class LinkRef:
    href: str
    anchor: str = ""
    nofollow: bool = False
    internal: bool = True


@dataclass(slots=True)
class ImageRef:
    src: str
    alt: str | None = None
    lazy: bool = False
    bytes: int | None = None


@dataclass(slots=True)
class PageRecord:
    """One crawled URL. Fields land across days 1-4; day 1 fills the top block."""

    # --- day 1: fetch layer ---
    url: str
    final_url: str = ""
    status: int | None = None
    redirect_chain: list[str] = field(default_factory=list)
    depth: int = 0
    discovered_via: list[str] = field(default_factory=list)
    response_ms: int | None = None
    content_type: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    fetch_error: str | None = None
    raw_html_len: int = 0

    # --- day 2: rendering ---
    rendered_html_len: int = 0
    render_timed_out: bool = False

    # --- day 3: parsing ---
    title: dict[str, Any] | None = None
    meta_description: dict[str, Any] | None = None
    canonical: str | None = None
    robots_meta: str | None = None
    h1: list[str] = field(default_factory=list)
    heading_outline: list[tuple[str, int]] = field(default_factory=list)
    word_count: int = 0
    images: list[ImageRef] = field(default_factory=list)
    internal_links: list[LinkRef] = field(default_factory=list)
    external_links: list[LinkRef] = field(default_factory=list)
    structured_data: list[dict[str, Any]] = field(default_factory=list)
    hreflang: list[dict[str, str]] = field(default_factory=list)
    og: dict[str, str] = field(default_factory=dict)

    # --- day 4: grouping ---
    template_signature: str | None = None

    fetched_at: str = field(default_factory=_now)

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    @property
    def is_html(self) -> bool:
        return "text/html" in self.content_type.lower()

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Finding:
    check_id: str
    severity: Severity
    pillar: Pillar
    title: str
    weight: int
    effort: Effort
    affected: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    remediation: str | None = None

    def __post_init__(self) -> None:
        # Evidence is required by design. A finding you cannot defend to a
        # client's developer should not be renderable.
        if not self.evidence:
            raise ValueError(f"{self.check_id}: evidence is required")


@dataclass(slots=True)
class CrawlConfig:
    domain: str
    max_pages: int = 300
    max_depth: int = 5
    concurrency: int = 3
    requests_per_second: float = 1.5
    timeout_s: float = 20.0
    render_timeout_s: float = 15.0
    render_settle_ms: int = 1500
    http2: bool = False
    size_images: bool = True
    obey_robots: bool = True
    user_agent: str = (
        "Mozilla/5.0 (compatible; CrawlspaceBot/1.0; +https://newbi.live/crawler)"
    )
