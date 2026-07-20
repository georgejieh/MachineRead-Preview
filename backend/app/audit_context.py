import asyncio
from dataclasses import dataclass

from app.fetching import FetchResult, fetch_url, make_root_url


@dataclass(frozen=True)
class AuditContext:
    url: str
    homepage: FetchResult
    robots: FetchResult
    sitemap: FetchResult


async def build_audit_context(url: str) -> AuditContext:
    homepage, robots, sitemap = await asyncio.gather(
        fetch_url(url),
        fetch_url(make_root_url(url, "/robots.txt")),
        fetch_url(make_root_url(url, "/sitemap.xml")),
    )
    return AuditContext(url=url, homepage=homepage, robots=robots, sitemap=sitemap)
