"""On-disk store for captured HTML.

Written so that re-parsing never means re-crawling. Day 3 will iterate this
store dozens of times while parse.py is being built; without it every parser
change would mean another polite 20-minute crawl of a client's site.
"""
from __future__ import annotations

import gzip
import hashlib
from pathlib import Path


class HtmlStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        (self.root / "raw").mkdir(parents=True, exist_ok=True)
        (self.root / "rendered").mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(url: str) -> str:
        # Content-addressed by URL: filesystem-safe, collision-free, and stable
        # across runs so two crawls of the same site line up for diffing.
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]

    def _path(self, kind: str, url: str) -> Path:
        return self.root / kind / f"{self.key(url)}.html.gz"

    def put(self, kind: str, url: str, html: str | bytes) -> None:
        if isinstance(html, str):
            html = html.encode("utf-8", "replace")
        self._path(kind, url).write_bytes(gzip.compress(html, 6))

    def get(self, kind: str, url: str) -> str | None:
        path = self._path(kind, url)
        if not path.exists():
            return None
        return gzip.decompress(path.read_bytes()).decode("utf-8", "replace")

    def has(self, kind: str, url: str) -> bool:
        return self._path(kind, url).exists()
