"""Intake: resolving which host the site actually lives on.

This runs before anything else, and getting it wrong poisons the whole audit.
If the canonical host is https://www.example.com and we crawl
https://example.com, every internal link looks like a cross-host redirect and
the redirect-chain check reports hundreds of findings that do not exist.

It is also a check in its own right: if more than one of the four host variants
returns 200 without redirecting, the site is serving duplicate content at up to
four addresses (CRW-14).
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from .urlnorm import normalise, registrable_host


@dataclass(slots=True)
class HostResolution:
    canonical: str
    variants: dict[str, dict] = field(default_factory=dict)
    resolving_variants: list[str] = field(default_factory=list)
    https_enforced: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def duplicate_hosts(self) -> bool:
        """More than one variant answering 200 without redirecting."""
        return len(self.resolving_variants) > 1


def _is_literal_host(host: str) -> bool:
    """True for an IP address or a name with no dots — localhost, a container."""
    if "." not in host:
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def host_variants(domain: str) -> list[str]:
    """The four forms every public site has, whether or not it means to.

    Two cases get one variant instead of four: an IP or bare hostname, which
    has no www form, and an explicit non-default port, where the port *is* the
    address and rewriting the scheme would point somewhere else entirely.
    Local test servers are both, which is how this was caught.
    """
    parts = urlsplit(domain if "//" in domain else f"https://{domain}")
    hostname = (parts.hostname or "").lower()
    scheme = parts.scheme or "https"

    default_port = {"http": 80, "https": 443}.get(scheme)
    if parts.port and parts.port != default_port:
        return [f"{scheme}://{hostname}:{parts.port}/"]

    if _is_literal_host(hostname):
        return [f"{scheme}://{hostname}/"]

    host = registrable_host(f"{scheme}://{hostname}")
    return [
        f"https://{host}/",
        f"https://www.{host}/",
        f"http://{host}/",
        f"http://www.{host}/",
    ]


async def resolve_host(client: httpx.AsyncClient, domain: str) -> HostResolution:
    """Probe all four variants and decide which one the site really uses."""
    variants = host_variants(domain)
    results: dict[str, dict] = {}

    for variant in variants:
        try:
            response = await client.get(variant, timeout=15.0,
                                        follow_redirects=True)
        except Exception as exc:
            # Deliberately broad, same reasoning as fetcher.py: this runs
            # before any page has been fetched, outside the per-page recovery
            # in crawler.py's worker loop, so an h2/ssl/anyio exception here —
            # not just an httpx.HTTPError — would crash the whole run before
            # it starts rather than costing one host variant.
            results[variant] = {"error": f"{type(exc).__name__}: {exc}"}
            continue

        final = str(response.url)
        results[variant] = {
            "status": response.status_code,
            "final_url": final,
            "redirected": normalise(final) != normalise(variant),
            "hops": len(response.history),
        }

    # A variant "resolves" if it answers 200 at its own address rather than
    # redirecting somewhere else. Two of those means duplicate content.
    resolving = [
        v for v, r in results.items()
        if r.get("status") == 200 and not r.get("redirected")
    ]

    canonical = _pick_canonical(results, variants)

    http_variants = [v for v in variants if v.startswith("http://")]
    https_exists = any(v.startswith("https://") for v in variants)
    https_enforced = (not https_exists) or all(
        results.get(v, {}).get("redirected", False)
        or results.get(v, {}).get("status") != 200
        for v in http_variants
    )

    resolution = HostResolution(
        canonical=canonical,
        variants=results,
        resolving_variants=resolving,
        https_enforced=https_enforced,
    )

    if resolution.duplicate_hosts:
        resolution.notes.append(
            f"{len(resolving)} host variants return 200 without redirecting: "
            + ", ".join(resolving)
        )
    if not https_enforced:
        resolution.notes.append("http:// does not redirect to https://")

    return resolution


def _pick_canonical(results: dict[str, dict], variants: list[str]) -> str:
    """Prefer where the site sends us, then https, then whatever answers."""
    # Where do the https variants end up? That is the site's own opinion.
    for variant in variants:
        if len(variants) > 1 and not variant.startswith("https://"):
            continue
        info = results.get(variant, {})
        if info.get("status") == 200:
            final = info.get("final_url") or variant
            normalised = normalise(final)
            if normalised:
                # Keep only the origin; the landing path is not the crawl root.
                parts = urlsplit(normalised)
                return f"{parts.scheme}://{parts.netloc}/"

    for variant in variants:
        if results.get(variant, {}).get("status") == 200:
            return variant

    return variants[0]
