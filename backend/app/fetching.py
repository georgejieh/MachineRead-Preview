import asyncio
import contextlib
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from app.crawler_registry import bot_user_agents
from app.ssrf import resolve_public_address

DEFAULT_USER_AGENT = "MachineRead/1.0"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

BOT_USER_AGENTS: dict[str, str] = bot_user_agents()


@contextlib.contextmanager
def _pinned_dns(hostname: str, address: str):
    """Pin DNS resolution of ``hostname`` to ``address`` for one request.

    Replaces ``socket.getaddrinfo`` so the underlying HTTP client connects
    to the validated address rather than re-resolving — closing the
    DNS-rebinding window between validation and connect. Restores the
    original resolver on exit even if the caller raises.
    """
    original = socket.getaddrinfo

    def pinned(host, *args, **kwargs):
        if host == hostname:
            return [(2, 1, 6, "", (address, 0))]
        return original(host, *args, **kwargs)

    socket.getaddrinfo = pinned
    try:
        yield
    finally:
        socket.getaddrinfo = original


@dataclass(frozen=True)
class FetchResult:
    requested_url: str
    final_url: str
    status_code: int | None
    headers: dict[str, str]
    text: str
    elapsed_ms: int
    redirect_chain: list[str]
    error: str | None = None

    @property
    def ok(self) -> bool:
        # 3xx is treated as OK because the HTTP client follows redirects by
        # default; arriving at a 3xx here means we hit the redirect cap
        # (FetchLimit.max_redirects) rather than the redirect itself being
        # successful. Callers that care about redirect-cap awareness should
        # inspect ``status_code`` and ``redirect_chain`` separately rather
        # than relying on ``ok`` alone.
        return self.error is None and self.status_code is not None and 200 <= self.status_code < 400

    @property
    def blocked(self) -> bool:
        return self.status_code in {401, 403, 429, 503}


def make_root_url(base_url: str, path: str) -> str:
    return urljoin(base_url, path)


def _response_headers(response: httpx.Response) -> dict[str, str]:
    return {key.lower(): value for key, value in response.headers.items()}


async def fetch_url(
    url: str,
    user_agent: str = DEFAULT_USER_AGENT,
    accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.7",
    timeout: float = 15.0,
    max_redirects: int = 5,
    method: str = "GET",
) -> FetchResult:
    """Fetch a public URL while validating every redirect target.

    ``method`` is HTTP verb (``GET`` by default). ``HEAD`` is supported as a
    lightweight probe for endpoints such as NLWeb's ``/ask``. All existing
    callers default to GET so behaviour is unchanged.
    """
    start = time.monotonic()
    method_upper = (method or "GET").upper()

    try:
        current_url, current_ip, _ = await asyncio.to_thread(resolve_public_address, url)
    except ValueError as exc:
        return FetchResult(url, url, None, {}, "", 0, [], str(exc))

    headers = {
        "User-Agent": user_agent,
        "Accept": accept,
    }
    redirect_chain: list[str] = []

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            for _ in range(max_redirects + 1):
                with _pinned_dns(urlparse(current_url).hostname or "", current_ip):
                    response = await client.request(method_upper, current_url, headers=headers)
                elapsed_ms = round((time.monotonic() - start) * 1000)
                final_url = str(response.url)

                if response.status_code not in {301, 302, 303, 307, 308}:
                    return FetchResult(
                        requested_url=url,
                        final_url=final_url,
                        status_code=response.status_code,
                        headers=_response_headers(response),
                        text=response.text,
                        elapsed_ms=elapsed_ms,
                        redirect_chain=redirect_chain,
                    )

                location = response.headers.get("location")
                if not location:
                    return FetchResult(
                        requested_url=url,
                        final_url=final_url,
                        status_code=response.status_code,
                        headers=_response_headers(response),
                        text=response.text,
                        elapsed_ms=elapsed_ms,
                        redirect_chain=redirect_chain,
                    )

                next_url = urljoin(final_url, location)
                try:
                    next_url, next_ip, _ = await asyncio.to_thread(resolve_public_address, next_url)
                except ValueError as exc:
                    return FetchResult(
                        requested_url=url,
                        final_url=final_url,
                        status_code=response.status_code,
                        headers=_response_headers(response),
                        text="",
                        elapsed_ms=elapsed_ms,
                        redirect_chain=redirect_chain,
                        error=f"Unsafe redirect target: {exc}",
                    )
                redirect_chain.append(next_url)
                current_url, current_ip = next_url, next_ip

    except httpx.RequestError as exc:
        elapsed_ms = round((time.monotonic() - start) * 1000)
        return FetchResult(url, current_url, None, {}, "", elapsed_ms, redirect_chain, str(exc))

    elapsed_ms = round((time.monotonic() - start) * 1000)
    return FetchResult(
        requested_url=url,
        final_url=current_url,
        status_code=None,
        headers={},
        text="",
        elapsed_ms=elapsed_ms,
        redirect_chain=redirect_chain,
        error="Too many redirects",
    )
