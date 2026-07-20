from app.audit_context import AuditContext
from app.fetching import FetchResult

EXAMPLE_URL = "https://example.com"
SHOP_URL = "https://shop.example"

DEFAULT_HTML = "<html><body>Example</body></html>"
ALLOW_ALL_ROBOTS = "User-agent: *\nAllow: /"
ROBOTS_WITH_SITEMAP = "User-agent: *\nAllow: /\nSitemap: https://example.com/sitemap.xml"
EMPTY_SITEMAP = "<urlset></urlset>"
BASIC_SITEMAP = "<urlset><url><loc>https://example.com</loc></url></urlset>"


def make_fetch_result(
    requested_url: str,
    text: str,
    *,
    final_url: str | None = None,
    status_code: int | None = 200,
    headers: dict[str, str] | None = None,
    elapsed_ms: int = 1,
    redirect_chain: list[str] | None = None,
    error: str | None = None,
) -> FetchResult:
    return FetchResult(
        requested_url=requested_url,
        final_url=final_url or requested_url,
        status_code=status_code,
        headers={} if headers is None else headers,
        text=text,
        elapsed_ms=elapsed_ms,
        redirect_chain=[] if redirect_chain is None else redirect_chain,
        error=error,
    )


def make_audit_context(
    *,
    base_url: str = EXAMPLE_URL,
    homepage_html: str = DEFAULT_HTML,
    homepage_headers: dict[str, str] | None = None,
    homepage_requested_url: str | None = None,
    homepage_final_url: str | None = None,
    robots_text: str = ALLOW_ALL_ROBOTS,
    robots_headers: dict[str, str] | None = None,
    sitemap_text: str = EMPTY_SITEMAP,
    sitemap_headers: dict[str, str] | None = None,
) -> AuditContext:
    root_url = base_url.rstrip("/")
    requested_url = homepage_requested_url or base_url
    return AuditContext(
        url=base_url,
        homepage=make_fetch_result(
            requested_url,
            homepage_html,
            final_url=homepage_final_url,
            headers={"content-type": "text/html"} if homepage_headers is None else homepage_headers,
        ),
        robots=make_fetch_result(
            f"{root_url}/robots.txt",
            robots_text,
            headers={"content-type": "text/plain"} if robots_headers is None else robots_headers,
        ),
        sitemap=make_fetch_result(
            f"{root_url}/sitemap.xml",
            sitemap_text,
            headers={"content-type": "application/xml"} if sitemap_headers is None else sitemap_headers,
        ),
    )
