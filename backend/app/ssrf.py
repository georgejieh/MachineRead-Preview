import ipaddress
import socket
from urllib.parse import urlparse


_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_private(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
        return any(ip in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        return False


def validate_url(url: str) -> str:
    """Return normalised URL or raise ValueError if unsafe."""
    parsed = urlparse(url)

    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Scheme '{parsed.scheme}' is not allowed")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL has no hostname")

    try:
        resolved = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve hostname: {exc}") from exc

    for _, _, _, _, sockaddr in resolved:
        address = sockaddr[0]
        if _is_private(address):
            raise ValueError(f"URL resolves to a private address: {address}")

    return url
