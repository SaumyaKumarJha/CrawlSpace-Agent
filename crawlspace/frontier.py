"""The crawl frontier: what to fetch next, and what we have already seen."""
from __future__ import annotations

import heapq
from dataclasses import dataclass

from .urlnorm import normalise, same_site, looks_non_html


@dataclass(slots=True, order=True)
class QueueItem:
    depth: int
    url: str
    source: str


class Frontier:
    """Breadth-first queue with a seen-set and scope guards.

    Breadth-first matters: it means a truncated crawl still covers the parts of
    the site closest to the homepage, which are the parts that matter. A
    depth-first crawl that hits the page cap gives you 300 pages of one blog
    category and nothing else.
    """

    def __init__(self, root: str, *, max_depth: int = 5, max_pages: int = 300) -> None:
        self.root = root
        self.max_depth = max_depth
        self.max_pages = max_pages
        self._heap: list[QueueItem] = []
        self._seen: set[str] = set()
        self._sources: dict[str, set[str]] = {}
        self.dispatched = 0
        self.skipped_offsite = 0
        self.skipped_depth = 0
        self.skipped_non_html = 0

    def __len__(self) -> int:
        return len(self._heap)

    @property
    def seen_count(self) -> int:
        return len(self._seen)

    def add(self, url: str, *, depth: int, source: str, base: str | None = None) -> bool:
        """Normalise, check scope, enqueue. Returns True if newly queued."""
        norm = normalise(url, base=base)
        if not norm:
            return False

        if norm in self._seen:
            # Already known, but record the additional discovery source —
            # "in the sitemap but never linked to" is the orphan-page finding.
            self._sources.setdefault(norm, set()).add(source)
            return False

        if not same_site(norm, self.root):
            self.skipped_offsite += 1
            return False
        if looks_non_html(norm):
            self.skipped_non_html += 1
            return False
        if depth > self.max_depth:
            self.skipped_depth += 1
            return False

        self._seen.add(norm)
        self._sources[norm] = {source}
        heapq.heappush(self._heap, QueueItem(depth=depth, url=norm, source=source))
        return True

    def pop(self) -> QueueItem | None:
        if not self._heap or self.dispatched >= self.max_pages:
            return None
        self.dispatched += 1
        return heapq.heappop(self._heap)

    def sources_for(self, url: str) -> list[str]:
        return sorted(self._sources.get(url, set()))

    def stats(self) -> dict[str, int]:
        return {
            "queued": len(self._heap),
            "seen": len(self._seen),
            "dispatched": self.dispatched,
            "skipped_offsite": self.skipped_offsite,
            "skipped_depth": self.skipped_depth,
            "skipped_non_html": self.skipped_non_html,
        }
