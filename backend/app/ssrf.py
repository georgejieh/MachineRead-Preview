import ipaddress
import socket
from urllib.parse import urlparse


def _is_private(address: str) -> bool:
    """Return True if ``address`` points at a non-public destination.

    Handles three categories of non-public addresses that a naive
    IPv4-only blocklist misses:

    - IPv4-mapped IPv6 (``::ffff:127.0.0.1``) unwraps to the embedded
      IPv4 so the global check applies to the real destination.
    - The unspecified address (``0.0.0.0`` / ``::``) routes to the local
      host on most platforms and is not a public IP.
    - Carrier-grade NAT (``100.64.0.0/10``) is treated as non-public
      because it is never a legitimate web destination for a public
      audit target.
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_unspecified:
        return True
    return not ip.is_global


def _resolve_addresses(hostname: str) -> list[tuple[str, int | None]]:
    try:
        resolved = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve hostname: {exc}") from exc
    return [(sockaddr[0], sockaddr[1] if len(sockaddr) > 1 else None) for *_, sockaddr in resolved]


def validate_url(url: str) -> str:
    """Return normalised URL or raise ValueError if unsafe."""
    parsed = urlparse(url)

    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Scheme '{parsed.scheme}' is not allowed")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL has no hostname")

    for address, _ in _resolve_addresses(hostname):
        if _is_private(address):
            raise ValueError(f"URL resolves to a private address: {address}")

    return url


def resolve_public_address(url: str) -> tuple[str, str, int | None]:
    """Return ``(url, address, port)`` for a public destination or raise.

    Resolves the hostname once, rejects the URL if any resolved address is
    private, and returns the first safe address so the caller can pin the
    connection to it and close the DNS-rebinding window between
    validation and connect.
    """
    parsed = urlparse(url)

    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Scheme '{parsed.scheme}' is not allowed")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL has no hostname")

    port = parsed.port
    addresses = _resolve_addresses(hostname)
    if not addresses:
        raise ValueError(f"Cannot resolve hostname: {hostname}")

    safe: tuple[str, int | None] | None = None
    for address, addr_port in addresses:
        if _is_private(address):
            raise ValueError(f"URL resolves to a private address: {address}")
        if safe is None:
            safe = (address, addr_port)
    return url, safe[0], port or safe[1]
