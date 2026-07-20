import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from app.audit_context import AuditContext
from app.checks.sitemap_analysis import analyse_sitemap_basic, sitemap_urls_from_robots
from app.fetching import FetchResult, fetch_url, make_root_url
from app.models import CheckResult

if TYPE_CHECKING:
    from app.fetch_evidence import FetchEvidence

_URL_PATTERN = re.compile(r"https?://[^\s)>\"]+")
_MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\([^)]+\)")
_WORD_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9'_-]*")
_MARKDOWN_ACCEPT = "text/markdown, text/plain;q=0.9, text/html;q=0.4,*/*;q=0.1"
_MARKDOWN_CONTENT_TYPES = ("text/markdown", "text/x-markdown", "text/plain")


@dataclass(frozen=True)
class MarkdownAccessResult:
    available: bool
    detail: str | None
    issues: list[str]
    hints: list[str]

_TEMPLATES = {
    "pass": (
        "Machine-readable discovery is healthy: {details}.",
        "No action needed.",
    ),
    "partial": (
        "Machine-readable discovery is partially configured: {details}.",
        "Improve the missing discovery signals. A strong setup has /llms.txt, "
        "valid sitemaps with lastmod dates, Sitemap entries in robots.txt, and "
        "a low-friction Markdown or text path.",
    ),
    "fail": (
        "No useful llms.txt or sitemap discovery was found.",
        "Create /llms.txt, publish a valid sitemap, add lastmod dates, and reference "
        "the sitemap from robots.txt. A Markdown fallback is also useful for agents.",
    ),
    "fetch_error": (
        "Could not check llms.txt or sitemap due to a network error.",
        "Ensure your site is publicly accessible.",
    ),
}


def _analyse_llms_txt(content: str) -> tuple[bool, list[str]]:
    text = content.strip()
    issues: list[str] = []
    if len(text) < 20:
        return False, ["too short"]
    if not text.startswith("#"):
        issues.append("missing H1 title")
    if not (_URL_PATTERN.search(text) or _MARKDOWN_LINK_PATTERN.search(text)):
        issues.append("no URLs or Markdown links")
    return not issues, issues


def _analyse_sitemap(content: str) -> tuple[bool, int, bool, bool]:
    return analyse_sitemap_basic(content)


def _has_vary_accept(headers: dict[str, str]) -> bool:
    vary = headers.get("vary", "")
    return any(token.strip().lower() in {"accept", "*"} for token in vary.split(","))


def _markdown_header_hints(headers: dict[str, str]) -> list[str]:
    hints: list[str] = []
    link_header = headers.get("link", "").lower()
    if "alternate" in link_header and (
        "text/markdown" in link_header
        or "text/plain" in link_header
        or ".md" in link_header
        or "llms" in link_header
    ):
        hints.append("Link header advertises Markdown/text alternate")

    content_location = headers.get("content-location", "").lower()
    if content_location.endswith((".md", ".markdown", ".txt")):
        hints.append("Content-Location points to text export")

    if "content-signal" in headers or "content-signals" in headers:
        hints.append("Content-Signal header present")

    for key, value in headers.items():
        header_text = f"{key}: {value}".lower()
        if key in {"content-type", "vary", "link", "content-location"}:
            continue
        if any(token in header_text for token in ("llms", "markdown", "agent", "token")):
            hints.append("agent or Markdown header hint present")
            break

    return hints


def _markdown_body_quality(text: str) -> tuple[bool, bool, list[str]]:
    sample = text.strip()[:6000]
    lowered = sample.lower()
    issues: list[str] = []
    if len(sample) < 80:
        issues.append("body is too short for useful Markdown/text")
    if lowered.startswith(("<!doctype html", "<html")):
        issues.append("body is HTML, not Markdown/text")
        return False, False, issues

    word_count = len(_WORD_PATTERN.findall(sample))
    has_structure = any(
        (
            sample.startswith("#"),
            bool(_MARKDOWN_LINK_PATTERN.search(sample)),
            bool(re.search(r"(?m)^[-*]\s+\S", sample)),
            bool(re.search(r"(?m)^```", sample)),
            bool(re.search(r"(?m)^\|.+\|$", sample)),
        )
    )
    meaningful = word_count >= 30 or len(sample) >= 200
    if not meaningful and "body is too short for useful Markdown/text" not in issues:
        issues.append("body is too short for useful Markdown/text")
    return meaningful, has_structure, issues


def _content_type_quality(content_type: str) -> tuple[bool, str, str | None]:
    lowered = content_type.lower()
    if any(token in lowered for token in _MARKDOWN_CONTENT_TYPES):
        if "text/markdown" in lowered or "text/x-markdown" in lowered:
            return True, "text/markdown", None
        return True, "text/plain", None
    if not content_type:
        return False, "missing Content-Type", "missing Content-Type"
    return False, content_type.split(";", 1)[0].strip(), "Content-Type is not text/markdown or text/plain"


def analyse_markdown_response(
    response: FetchResult,
    label: str,
    require_vary_accept: bool = False,
) -> MarkdownAccessResult:
    if not response.ok:
        status = response.status_code if response.status_code is not None else "network error"
        return MarkdownAccessResult(False, None, [f"{label} returned {status}"], [])

    headers = {key.lower(): value for key, value in response.headers.items()}
    content_type = headers.get("content-type", "")
    content_type_ok, content_type_label, content_type_issue = _content_type_quality(content_type)
    body_meaningful, has_markdown_structure, body_issues = _markdown_body_quality(response.text)
    issues = [*body_issues]
    hints = _markdown_header_hints(headers)

    if content_type_issue:
        issues.append(content_type_issue)

    vary_accept = _has_vary_accept(headers)
    if require_vary_accept and not vary_accept:
        issues.append("Vary: Accept is missing for negotiated Markdown/text")

    html_body = "body is HTML, not Markdown/text" in body_issues
    available = (
        body_meaningful
        and not html_body
        and (content_type_ok or (not content_type and has_markdown_structure))
    )
    if not available:
        return MarkdownAccessResult(False, None, issues, hints)

    detail_parts = [f"{label} returns {content_type_label} with meaningful body"]
    if require_vary_accept and vary_accept:
        detail_parts.append("Vary: Accept present")
    if has_markdown_structure:
        detail_parts.append("Markdown structure detected")
    if hints:
        detail_parts.extend(hints[:2])
    return MarkdownAccessResult(True, "; ".join(detail_parts), issues, hints)


async def best_sitemap(context: AuditContext) -> tuple[bool, int, bool, bool, bool]:
    candidates = [context.sitemap]
    robots_sitemaps = sitemap_urls_from_robots(context.robots.text) if context.robots.ok else []
    for sitemap_url in robots_sitemaps[:3]:
        candidates.append(await fetch_url(urljoin(context.url, sitemap_url)))

    best = (False, 0, False, False)
    for candidate in candidates:
        if candidate.ok:
            analysed = _analyse_sitemap(candidate.text)
            if analysed[1] > best[1]:
                best = analysed

    has_robot_reference = bool(robots_sitemaps)
    return best[0], best[1], best[2], best[3], has_robot_reference


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped


async def markdown_access_details(context: AuditContext) -> MarkdownAccessResult:
    issues: list[str] = []
    negotiated = await fetch_url(context.url, accept=_MARKDOWN_ACCEPT)
    negotiated_result = analyse_markdown_response(
        negotiated,
        "homepage Markdown negotiation",
        require_vary_accept=True,
    )
    if negotiated_result.available:
        return negotiated_result
    issues.extend(negotiated_result.issues[:2])

    for path in ("/index.md", "/README.md"):
        response = await fetch_url(make_root_url(context.url, path), accept=_MARKDOWN_ACCEPT)
        result = analyse_markdown_response(response, f"{path} fallback")
        if result.available:
            return result
        issues.extend(result.issues[:1])

    fallback_issue = (
        "no negotiated Markdown/plain response or direct Markdown fallback returned "
        "meaningful text with a Markdown/plain Content-Type"
    )
    return MarkdownAccessResult(False, None, _dedupe([fallback_issue, *issues])[:4], [])


async def agent_text_access(context: AuditContext) -> tuple[bool, list[str]]:
    details: list[str] = []
    llms_resp = await fetch_url(make_root_url(context.url, "/llms.txt"))
    if llms_resp.ok:
        llms_valid, _ = _analyse_llms_txt(llms_resp.text)
        if llms_valid:
            details.append("valid llms.txt")

    markdown_result = await markdown_access_details(context)
    if markdown_result.available and markdown_result.detail:
        details.append(markdown_result.detail)

    return bool(details), details


async def check_llms_txt(
    context: AuditContext,
    qa2_evidence: "FetchEvidence | None" = None,
) -> CheckResult:
    """Check for llms.txt and sitemap discovery quality."""
    llms_resp = (
        qa2_evidence.llms_response
        if qa2_evidence is not None
        else await fetch_url(make_root_url(context.url, "/llms.txt"))
    )

    if llms_resp.error and context.sitemap.error and context.robots.error:
        finding, fix = _TEMPLATES["fetch_error"]
        return CheckResult(
            pillar="scrapability",
            check_name="llms_txt",
            label="llms.txt and Sitemap",
            state="warn",
            evidence_level="unknown",
            score=0,
            max_score=5,
            finding=finding,
            fix=fix,
            effort="low",
        )

    has_llms = llms_resp.ok
    llms_valid = False
    llms_issues: list[str] = []
    if has_llms:
        llms_valid, llms_issues = _analyse_llms_txt(llms_resp.text)

    if qa2_evidence is None:
        (
            sitemap_valid,
            url_count,
            sitemap_has_lastmod,
            is_sitemap_index,
            has_robot_reference,
        ) = await best_sitemap(context)
        markdown_result = await markdown_access_details(context)
    else:
        (
            sitemap_valid,
            url_count,
            sitemap_has_lastmod,
            is_sitemap_index,
            has_robot_reference,
        ) = qa2_evidence.sitemap_score_evidence
        markdown_result = qa2_evidence.homepage_markdown_result

    score = 0
    details: list[str] = []
    if llms_valid:
        score += 2
        details.append("valid llms.txt")
    elif has_llms:
        score += 1
        details.append(f"llms.txt needs work ({', '.join(llms_issues)})")

    if sitemap_valid:
        score += 1
        sitemap_label = f"sitemap has {url_count} URL(s)"
        if is_sitemap_index:
            sitemap_label = f"sitemap index references {url_count} sitemap URL(s)"
        details.append(sitemap_label)
    if sitemap_has_lastmod:
        score += 1
        details.append("lastmod dates present")
    if has_robot_reference:
        score += 1
        details.append("robots.txt references sitemap")
    if markdown_result.available and markdown_result.detail:
        score += 1
        details.append(markdown_result.detail)

    score = min(score, 5)

    if score >= 4:
        state = "pass"
        finding, fix = _TEMPLATES["pass"]
    elif score > 0:
        state = "partial"
        finding, fix = _TEMPLATES["partial"]
    else:
        state = "fail"
        finding, fix = _TEMPLATES["fail"]

    if details and "{details}" in finding:
        finding = finding.format(details="; ".join(details))

    if markdown_result.issues:
        caveat = "; ".join(markdown_result.issues[:3])
        finding += f" Markdown/text caveat: {caveat}."
        markdown_fix = (
            "Serve substantial Markdown as text/markdown or text/plain, include "
            "Vary: Accept when using content negotiation, and expose /index.md "
            "or another clear fallback when negotiation is not available."
        )
        fix = markdown_fix if fix == "No action needed." else f"{fix} {markdown_fix}"

    if qa2_evidence is not None:
        extraction = qa2_evidence.extraction_readiness
        finding += (
            " Extraction-readiness caveat: this is a local source-response proxy; "
            "no Firecrawl or other extraction-provider API was called."
        )
        if (
            extraction.markdown_usable_count
            and extraction.best_markdown_token_coverage_ratio < 0.5
        ):
            finding += (
                " The best usable Markdown response overlaps less than half of the "
                "bounded homepage content tokens."
            )
            overlap_fix = (
                " Keep the Markdown export aligned with the visible homepage's primary "
                "content and headings."
            )
            fix = overlap_fix if fix == "No action needed." else fix + overlap_fix

    return CheckResult(
        pillar="scrapability",
        check_name="llms_txt",
        label="LLM Text & Markdown Access",
        state=state,
        score=score,
        max_score=5,
        finding=finding,
        fix=fix,
        effort="low",
    )
