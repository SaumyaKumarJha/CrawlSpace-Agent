"""Tests for Crawler orchestration bugs, not the full crawl loop.

The full crawl loop needs a live server (see the fixture-site integration
runs); these tests isolate the two bugs found on the real Azaya crawl that
don't need a network at all: the image-sizing step running serially through
the page crawl's rate limiter, and results not being shared across every
record referencing the same image.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from crawlspace.crawler import Crawler, IMAGE_SIZE_CONCURRENCY
from crawlspace.models import CrawlConfig, ImageRef, PageRecord


def make_crawler(tmp_path, *, requests_per_second: float = 1.0) -> Crawler:
    config = CrawlConfig(domain="https://example.com/", requests_per_second=requests_per_second)
    return Crawler(config, tmp_path / "out", render=False)


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
        assert elapsed < expected_batches * 0.2 + 2.0
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
