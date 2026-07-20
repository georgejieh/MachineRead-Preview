"""DNS pinning for httpx AsyncClient.

Replaces the broken ``socket.getaddrinfo`` monkeypatch approach with a
custom AsyncHTTPTransport that overrides the per-request connect path
to use a previously validated IP. The fix is per-async-task via
``ContextVar`` so concurrent requests in the same event loop cannot
trample each other's pin state.

The transport reads a ``pinned_dns`` context variable set by the
caller to a ``(hostname, address)`` pair. When a request's host matches
``hostname``, the request is rewritten so the TCP socket connects to
``address`` while the Host header and TLS SNI hostname both carry
``hostname`` — the standard pattern for IP-literal connection with
hostname-based virtual hosting and certificate validation.

DNS resolution is bypassed entirely when a pin is active. When no pin
is set, behaviour matches the default AsyncHTTPTransport: the
underlying httpcore connection pool resolves DNS through whatever
resolver the operating system provides.
"""

from __future__ import annotations

import contextvars
from typing import Tuple

import httpx

# Per-async-task pin. Setting this for an async task causes the next
# httpx request whose host matches ``hostname`` to connect to
# ``address`` instead of re-resolving. The variable is private to the
# running coroutine, so concurrent fetches in the same event loop do
# not interfere with each other.
pinned_dns: contextvars.ContextVar[Tuple[str, str] | None] = contextvars.ContextVar(
    "pinned_dns", default=None
)


class PinnedHTTPTransport(httpx.AsyncHTTPTransport):
    """AsyncHTTPTransport that honours the ``pinned_dns`` ContextVar.

    When ``pinned_dns`` is set to ``(hostname, address)`` and a request
    targets that hostname, the transport builds an httpcore URL with
    ``address`` as the host (so the TCP socket connects to the pinned
    IP) and sets:

    - ``Host`` header to the original ``hostname`` (so the server's
      virtual-host routing sees the right name)
    - ``sni_hostname`` extension to the original ``hostname`` (so the
      TLS handshake validates the certificate against the right name)
    """

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        pin = pinned_dns.get()
        if pin is not None and request.url.host:
            pinned_hostname, pinned_address = pin
            if request.url.host == pinned_hostname:
                request = _rewrite_for_pinned_ip(request, pinned_address)
        return await super().handle_async_request(request)


def _rewrite_for_pinned_ip(request: httpx.Request, pinned_address: str) -> httpx.Request:
    """Return a copy of ``request`` whose TCP host is the pinned IP.

    The copy carries the original hostname in the Host header and TLS
    SNI, so virtual hosting and certificate validation see the right
    name while the underlying socket connects to the validated address.
    """
    original_host = request.url.host
    original_scheme = request.url.scheme
    original_port = request.url.port
    original_path = request.url.raw_path.decode("ascii")
    original_https = original_scheme == "https"

    # Build a new URL that points at the pinned IP but keeps the
    # original scheme, port, path, and query string. We pass the full
    # raw path (which already includes the query) to httpx.URL so it
    # parses correctly without us re-assembling it.
    raw_with_query = request.url.raw_path
    # ``raw_path`` may be bytes (httpx.URL stores it that way) — decode
    # only for string interpolation into the URL constructor.
    if isinstance(raw_with_query, bytes):
        raw_with_query_str = raw_with_query.decode("ascii")
    else:
        raw_with_query_str = raw_with_query

    ip_url = httpx.URL(
        f"{original_scheme}://{pinned_address}{raw_with_query_str}"
    )

    # Carry the Host header through with the original hostname so the
    # server's virtual-host routing matches the SNI. The default Host
    # header that httpcore would generate from the rewritten URL
    # would otherwise be the IP literal.
    headers = [
        (name, value) for (name, value) in request.headers.items()
        if name.lower() != "host"
    ]
    needs_port = (
        original_port is not None
        and original_port != 0
        and not (
            (original_https and original_port == 443)
            or (not original_https and original_port == 80)
        )
    )
    host_header = f"{original_host}:{original_port}" if needs_port else original_host
    headers.append(("Host", host_header))

    # Carry extensions through and set sni_hostname so the TLS handshake
    # validates the certificate against the original hostname.
    extensions = dict(request.extensions)
    extensions["sni_hostname"] = original_host

    return httpx.Request(
        method=request.method,
        url=ip_url,
        headers=headers,
        content=request.content,
        extensions=extensions,
    )
