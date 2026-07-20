from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.audit_context import AuditContext
from app.fetching import fetch_url
from app.models import CheckResult

_TEMPLATES = {
    "pass": (
        "HTTPS redirect is active, canonical tag is self-referencing, and no "
        "www/non-www duplicate surface was detected.",
        "No action needed.",
    ),
    "partial": (
        "Canonical configuration has minor issues: {issues}. These can cause "
        "duplicate content signals that dilute ranking authority.",
        "Fix the canonical tag, prefer HTTPS, and redirect duplicate host variants "
        "to the canonical host.",
    ),
    "fail": (
        "Canonical configuration has critical issues: {issues}. Crawlers may index "
        "the wrong URL variant or split authority across duplicates.",
        "Set up a permanent redirect from HTTP to HTTPS and add a canonical tag "
        "to every public page.",
    ),
    "fetch_error": (
        "Could not check canonical configuration due to a network error.",
        "Ensure the site is publicly accessible.",
    ),
}


def _normalised_url(url: str) -> str:
    parsed = urlparse(urljoin(url, ""))
    return parsed._replace(fragment="", query="").geturl().rstrip("/")


def _alternate_host(url: str) -> str | None:
    parsed = urlparse(url)
    if not parsed.hostname:
        return None

    if parsed.hostname.startswith("www."):
        host = parsed.hostname.removeprefix("www.")
    else:
        host = "www." + parsed.hostname

    netloc = host
    if parsed.port:
        netloc = f"{host}:{parsed.port}"
    return parsed._replace(netloc=netloc).geturl()


async def check_canonical(context: AuditContext) -> CheckResult:
    """Check HTTPS redirect, canonical tag, and duplicate host variants."""
    if not context.homepage.ok:
        finding, fix = _TEMPLATES["fetch_error"]
        return CheckResult(
            pillar="seo",
            check_name="canonical",
            label="Canonical & HTTPS",
            state="warn",
            evidence_level="unknown",
            score=0,
            max_score=5,
            finding=finding,
            fix=fix,
            effort="low",
        )

    issues: list[str] = []
    parsed = urlparse(context.url)
    http_url = parsed._replace(scheme="http").geturl()
    http_fetch = await fetch_url(http_url)
    if http_fetch.ok and not http_fetch.final_url.startswith("https://"):
        issues.append("HTTP version serves content without redirecting to HTTPS")
    elif http_fetch.redirect_chain and not http_fetch.final_url.startswith("https://"):
        issues.append("HTTP does not redirect to HTTPS")

    soup = BeautifulSoup(context.homepage.text, "lxml")
    canonical_tag = soup.find("link", attrs={"rel": "canonical"})
    if not canonical_tag:
        issues.append("no canonical tag found")
    else:
        canonical_href = canonical_tag.get("href", "")
        canonical_url = urljoin(context.homepage.final_url, canonical_href)
        if _normalised_url(canonical_url) != _normalised_url(context.homepage.final_url):
            issues.append("canonical tag does not match final URL")

    alternate_url = _alternate_host(context.homepage.final_url)
    if alternate_url:
        alternate_fetch = await fetch_url(alternate_url)
        if alternate_fetch.ok:
            final_host = urlparse(context.homepage.final_url).hostname
            alternate_host = urlparse(alternate_fetch.final_url).hostname
            if alternate_host and final_host and alternate_host != final_host:
                issues.append("www/non-www variant also serves content")

    if not issues:
        state = "pass"
        score = 5
        finding, fix = _TEMPLATES["pass"]
    elif len(issues) == 1:
        state = "partial"
        score = 3
        finding, fix = _TEMPLATES["partial"]
        finding = finding.format(issues=issues[0])
    else:
        state = "fail"
        score = 1
        finding, fix = _TEMPLATES["fail"]
        finding = finding.format(issues="; ".join(issues))

    return CheckResult(
        pillar="seo",
        check_name="canonical",
        label="Canonical & HTTPS",
        state=state,
        score=score,
        max_score=5,
        finding=finding,
        fix=fix,
        effort="low",
    )
