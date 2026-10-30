import httpx

from crawlspace.intake import host_variants, resolve_host


class _ProtocolLevelError(Exception):
    """Stands in for an h2/ssl/anyio exception that does not subclass
    httpx.HTTPError — the exact shape of the crash that killed the first
    big client crawl (h2.exceptions.InvalidBodyLengthError isn't an httpx error either).
    """


async def test_resolve_host_survives_a_non_httpx_exception():
    """Before the fix, only httpx.HTTPError was caught per variant — anything
    else (a raw h2/ssl/anyio exception) would propagate out of resolve_host
    and crash the whole run before a single page was fetched. This reproduces
    that shape of failure and checks it now degrades to a per-variant error
    instead."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "https":
            raise _ProtocolLevelError("malformed frame")
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resolution = await resolve_host(client, "example.com")

    # Did not raise — every https variant recorded as an error, http still resolved.
    assert resolution.canonical.startswith("http://")
    https_results = [v for k, v in resolution.variants.items() if k.startswith("https://")]
    assert all("_ProtocolLevelError" in v.get("error", "") for v in https_results)


def test_produces_four_variants():
    v = host_variants("example.com")
    assert v == [
        "https://example.com/",
        "https://www.example.com/",
        "http://example.com/",
        "http://www.example.com/",
    ]


def test_www_input_normalises_to_the_same_four():
    assert host_variants("https://www.example.com/shop") == host_variants("example.com")
