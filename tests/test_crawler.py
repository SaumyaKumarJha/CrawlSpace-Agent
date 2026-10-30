"""Tests for Crawler orchestration bugs, not the full crawl loop.

The full crawl loop needs a live server (see the fixture-site integration
runs); these tests isolate the two bugs found on the first real client crawl that
don't need a network at all: the image-sizing step running serially through
the page crawl's rate limiter, and results not being shared across every
record referencing the same image.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict

import httpx
import pytest

from crawlspace.crawler import Crawler, IMAGE_SIZE_CONCURRENCY
from crawlspace.models import CrawlConfig, ImageRef, LinkRef, PageRecord


def make_crawler(tmp_path, *, requests_per_second: float = 1.0, resume: bool = False,
                  out_dir=None) -> Crawler:
    config = CrawlConfig(domain="https://example.com/", requests_per_second=requests_per_second)
    return Crawler(config, out_dir or tmp_path / "out", render=False, resume=resume)


def _slow_head_transport(delay_s: float) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(delay_s)
        return httpx.Response(200, headers={"content-length": "12345"})

    return httpx.MockTransport(handler)


class TestImageSizing:
    @pytest.mark.asyncio
    async def test_sizes_run_concurrently_not_serially(self, tmp_path):
        """20 images at 0.2s each: serial through a 1 req/s page limiter would
        take 20s (rate-limit floor) or 4s (raw latency floor). Concurrent
        sizing on its own limiter should finish in well under a second per
        batch of IMAGE_SIZE_CONCURRENCY, i.e. a small multiple of 0.2s."""
        crawler = make_crawler(tmp_path, requests_per_second=1.0)
        n_images = 20
        record = PageRecord(url="https://example.com/")
        record.images = [ImageRef(src=f"https://cdn.example.com/img{i}.jpg")
                          for i in range(n_images)]
        crawler.records = [record]

        transport = _slow_head_transport(0.2)
        async with httpx.AsyncClient(transport=transport) as client:
            start = time.monotonic()
            await crawler._size_images(client)
            elapsed = time.monotonic() - start

        # Serial through the old shared page-crawl limiter (1 req/s) would be
        # ~20s. Concurrent batches of IMAGE_SIZE_CONCURRENCY at 0.2s latency
        # should be a small number of seconds at most.
        expected_batches = -(-n_images // IMAGE_SIZE_CONCURRENCY)  # ceil
        assert elapsed < expected_batches * 0.2 + 4.0
        assert all(img.bytes == 12345 for img in record.images)

    @pytest.mark.asyncio
    async def test_bytes_shared_across_every_record_referencing_the_image(self, tmp_path):
        """The same hero image on 5 pages should be HEAD-requested once, and
        every ImageRef pointing at it should end up with the same size."""
        crawler = make_crawler(tmp_path)
        shared_src = "https://cdn.example.com/hero.jpg"
        requested_urls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requested_urls.append(str(request.url))
            return httpx.Response(200, headers={"content-length": "99999"})

        records = []
        for i in range(5):
            r = PageRecord(url=f"https://example.com/page{i}")
            r.images = [ImageRef(src=shared_src)]
            records.append(r)
        crawler.records = records

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await crawler._size_images(client)

        assert requested_urls == [shared_src]      # exactly one HEAD, not five
        assert all(r.images[0].bytes == 99999 for r in records)

    @pytest.mark.asyncio
    async def test_a_failing_image_does_not_block_the_others(self, tmp_path):
        crawler = make_crawler(tmp_path)
        record = PageRecord(url="https://example.com/")
        record.images = [
            ImageRef(src="https://cdn.example.com/broken.jpg"),
            ImageRef(src="https://cdn.example.com/fine.jpg"),
        ]
        crawler.records = [record]

        async def handler(request: httpx.Request) -> httpx.Response:
            if "broken" in str(request.url):
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(200, headers={"content-length": "500"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await crawler._size_images(client)

        assert record.images[0].bytes is None
        assert record.images[1].bytes == 500


class TestImageStats:
    def test_counts_each_image_once_not_once_per_page(self, tmp_path):
        crawler = make_crawler(tmp_path)
        for n in range(3):  # the same hero image on three pages
            crawler.records.append(PageRecord(url=f"https://example.com/{n}", images=[
                ImageRef(src="https://example.com/hero.jpg", bytes=500_000),
                ImageRef(src=f"https://example.com/thumb{n}.jpg", bytes=10_000),
            ]))
        crawler._pages_fh.close()

        stats = crawler.image_stats()

        assert stats["unique_images"] == 4
        assert stats["sized"] == 4
        assert stats["over_200kb"] == 1
        assert stats["sized"] <= stats["unique_images"]


class TestResume:
    """Day 5: an interrupted crawl resumes instead of restarting.

    These test the resume machinery directly (loading pages.jsonl back into
    PageRecords and replaying it into the frontier) rather than through a
    live crawl, which needs a server — that path is covered by the fixture
    site integration run.
    """

    @staticmethod
    def _write_pages_jsonl(out_dir, records: list[PageRecord]) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        with (out_dir / "pages.jsonl").open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(asdict(r), default=str) + "\n")

    def test_fresh_run_truncates_any_stale_pages_jsonl(self, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "pages.jsonl").write_text("leftover from a previous domain\n")

        crawler = make_crawler(tmp_path, out_dir=out_dir, resume=False)

        assert crawler.resumed is False
        assert crawler.records == []
        # __init__ opens in "w" mode — the stale line is gone once opened.
        crawler._pages_fh.close()
        assert out_dir.joinpath("pages.jsonl").read_text() == ""

    def test_resume_false_with_no_prior_file_behaves_like_a_fresh_crawl(self, tmp_path):
        crawler = make_crawler(tmp_path, resume=True)   # nothing to resume from
        assert crawler.resumed is False
        assert crawler.records == []

    def test_resume_loads_prior_records_and_does_not_truncate_them(self, tmp_path):
        out_dir = tmp_path / "out"
        prior = [PageRecord(url="https://example.com/", status=200, depth=0),
                 PageRecord(url="https://example.com/about", status=200, depth=1)]
        self._write_pages_jsonl(out_dir, prior)

        crawler = make_crawler(tmp_path, out_dir=out_dir, resume=True)

        assert crawler.resumed is True
        assert len(crawler.records) == 2
        assert {r.url for r in crawler.records} == {
            "https://example.com/", "https://example.com/about"
        }
        # Appending, not overwriting: write one more record and check both
        # the old lines and the new one survive on disk.
        crawler._append(PageRecord(url="https://example.com/contact", status=200, depth=1))
        crawler._pages_fh.close()
        lines = out_dir.joinpath("pages.jsonl").read_text().splitlines()
        assert len(lines) == 3

    def test_resumed_record_links_reconstruct_as_linkref_not_dicts(self, tmp_path):
        out_dir = tmp_path / "out"
        r = PageRecord(url="https://example.com/", status=200, depth=0)
        r.internal_links = [LinkRef(href="https://example.com/about")]
        r.images = [ImageRef(src="https://example.com/hero.jpg", bytes=500)]
        self._write_pages_jsonl(out_dir, [r])

        crawler = make_crawler(tmp_path, out_dir=out_dir, resume=True)

        resumed = crawler.records[0]
        assert isinstance(resumed.internal_links[0], LinkRef)
        assert resumed.internal_links[0].href == "https://example.com/about"
        assert isinstance(resumed.images[0], ImageRef)
        assert resumed.images[0].bytes == 500

    def test_a_truncated_last_line_from_the_crash_is_skipped_not_fatal(self, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        good = PageRecord(url="https://example.com/", status=200, depth=0)
        with (out_dir / "pages.jsonl").open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(good), default=str) + "\n")
            fh.write('{"url": "https://example.com/half-writ')   # crash mid-line

        crawler = make_crawler(tmp_path, out_dir=out_dir, resume=True)

        assert len(crawler.records) == 1
        assert crawler.records[0].url == "https://example.com/"

    def test_replay_marks_resumed_urls_seen_so_root_reseed_is_a_noop(self, tmp_path):
        out_dir = tmp_path / "out"
        prior = [PageRecord(url="https://example.com/", status=200, depth=0)]
        self._write_pages_jsonl(out_dir, prior)
        crawler = make_crawler(tmp_path, out_dir=out_dir, resume=True)

        crawler._replay_resumed_into_frontier()
        added = crawler.frontier.add("https://example.com/", depth=0, source="seed")

        assert added is False                      # already seen — not re-queued
        assert crawler.frontier.dispatched == 1     # budget already spent, accounted for

    def test_replay_still_queues_links_the_resumed_page_found_but_never_reached(self, tmp_path):
        out_dir = tmp_path / "out"
        r = PageRecord(url="https://example.com/", status=200, depth=0)
        r.internal_links = [LinkRef(href="https://example.com/never-crawled")]
        self._write_pages_jsonl(out_dir, [r])
        crawler = make_crawler(tmp_path, out_dir=out_dir, resume=True)

        crawler._replay_resumed_into_frontier()

        assert "https://example.com/never-crawled" in crawler.frontier._seen
        assert len(crawler.frontier) == 1           # sitting in the queue, ready to pop

    def test_a_page_that_links_to_another_resumed_page_is_not_requeued(self, tmp_path):
        """Root links to /about — both were already fetched. Root replays
        first (it's earlier in pages.jsonl); if the replay marked pages seen
        and replayed links in the same pass, root's link to /about would be
        queued before /about's own record marks it seen a moment later, and
        /about would be fetched a second time. This is the exact bug caught
        against the local fixture server, reproduced without a network."""
        out_dir = tmp_path / "out"
        root = PageRecord(url="https://example.com/", status=200, depth=0)
        root.internal_links = [LinkRef(href="https://example.com/about")]
        about = PageRecord(url="https://example.com/about", status=200, depth=1)
        self._write_pages_jsonl(out_dir, [root, about])

        crawler = make_crawler(tmp_path, out_dir=out_dir, resume=True)
        crawler._replay_resumed_into_frontier()

        assert len(crawler.frontier) == 0            # nothing queued — both already fetched
        assert crawler.frontier.stats()["seen"] == 2
