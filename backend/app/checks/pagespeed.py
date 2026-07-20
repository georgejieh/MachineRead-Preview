from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

from app.audit_context import AuditContext
from app.models import CheckResult

_RESPONSE_GOOD = 800
_RESPONSE_POOR = 2500

_TEMPLATES = {
    "pass": (
        "Included crawl-efficiency and HTML performance proxy signals pass: fast response "
        "({elapsed}ms), cache validators, minimal render-blocking stylesheets, sized media, "
        "minimal synchronous scripts in <head>, and mobile viewport basics. {mobile_detail}",
        "No action needed on the structural signals. Confirm real Core Web Vitals "
        "with Search Console or PageSpeed Insights.",
    ),
    "partial": (
        "Some included HTML performance proxy signals need attention: {issues}. These are "
        "structural proxies, not Lighthouse, CrUX, or real Core Web Vitals measurements.",
        "Fix the flagged issues: defer render-blocking resources, add image/video "
        "dimensions, use async/defer for scripts, publish a mobile viewport meta tag, "
        "and improve response time if high.",
    ),
    "weak_proxy": (
        "The included HTML performance proxy is weak: {issues}. This is not proof that "
        "the site fails Core Web Vitals because field and lab data require advanced verification.",
        "Use this as a triage hint. Confirm real performance with Search Console, "
        "CrUX, Lighthouse, or advanced performance coverage before treating it as an SEO defect.",
    ),
    "fetch_error": (
        "Could not fetch the page to measure performance signals.",
        "Ensure the site is publicly accessible and try again.",
    ),
}


@dataclass(frozen=True)
class MobileViewportSignal:
    score: int
    issues: list[str]
    positives: list[str]


def _count_render_blocking_stylesheets(head: Tag | None) -> int:
    if head is None:
        return 0
    count = 0
    for link in head.find_all("link", rel=lambda value: value and "stylesheet" in value):
        media = link.get("media", "all")
        if media in ("all", "", None):
            count += 1
    return count


def _count_unsized_media(soup: BeautifulSoup) -> int:
    count = 0
    for tag in soup.find_all(["img", "video"]):
        has_width = tag.get("width") or "width" in tag.get("style", "")
        has_height = tag.get("height") or "height" in tag.get("style", "")
        if not has_width or not has_height:
            count += 1
    return count


def _count_sync_head_scripts(head: Tag | None) -> int:
    if head is None:
        return 0
    count = 0
    for script in head.find_all("script"):
        if script.get("src") and not script.get("async") and not script.get("defer"):
            if script.get("type", "") != "module":
                count += 1
    return count


def _score_signal(value: int, good_threshold: int, poor_threshold: int) -> int:
    if value <= good_threshold:
        return 4
    if value <= poor_threshold:
        return 2
    return 0


def _has_recrawl_validator(headers: dict[str, str]) -> bool:
    return bool(headers.get("etag") or headers.get("last-modified"))


def _has_cache_policy(headers: dict[str, str]) -> bool:
    cache_control = headers.get("cache-control", "").lower()
    return bool(cache_control or headers.get("expires"))


def _body_size_ok(headers: dict[str, str], text: str) -> bool:
    limit = 15 * 1024 * 1024
    content_length = headers.get("content-length")
    if content_length and content_length.isdigit():
        return int(content_length) < limit
    return len(text.encode("utf-8", errors="ignore")) < limit


def _viewport_tokens(content: str) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for part in content.split(","):
        key, separator, value = part.strip().partition("=")
        if key and separator:
            tokens[key.strip().lower()] = value.strip().lower()
    return tokens


def _scale_value(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _has_responsive_html_hint(soup: BeautifulSoup) -> bool:
    if soup.find("picture"):
        return True
    if soup.find(["img", "source"], attrs={"srcset": True}) or soup.find(
        ["img", "source"],
        attrs={"sizes": True},
    ):
        return True

    head = soup.find("head")
    if head:
        for link in head.find_all("link", rel=lambda value: value and "stylesheet" in value):
            media = link.get("media", "").strip().lower()
            if media and media not in {"all", "screen"}:
                return True

    for style in soup.find_all("style"):
        css = style.get_text(" ", strip=True).lower()
        if "@media" in css or "min-width" in css or "max-width" in css:
            return True
    return False


def _mobile_viewport_signal(soup: BeautifulSoup) -> MobileViewportSignal:
    head = soup.find("head")
    viewport_tags = []
    if head:
        viewport_tags = [
            tag
            for tag in head.find_all("meta")
            if tag.get("name", "").strip().lower() == "viewport"
        ]

    if not viewport_tags:
        return MobileViewportSignal(0, ["missing mobile viewport meta tag"], [])

    issues: list[str] = []
    score = 4
    if len(viewport_tags) > 1:
        issues.append("multiple mobile viewport meta tags")
        score = min(score, 2)

    tokens = _viewport_tokens(viewport_tags[0].get("content", ""))
    if tokens.get("width") != "device-width":
        issues.append("mobile viewport does not set width=device-width")
        score = min(score, 2)

    maximum_scale = _scale_value(tokens.get("maximum-scale"))
    if tokens.get("user-scalable") in {"no", "0"} or (maximum_scale is not None and maximum_scale <= 1):
        issues.append("mobile viewport restricts pinch zoom")
        score = min(score, 2)

    positives: list[str] = []
    if not issues:
        positives.append("mobile viewport sets width=device-width without zoom restrictions")
    if _has_responsive_html_hint(soup):
        positives.append("sampled HTML exposes responsive layout or image hints")
    else:
        positives.append("responsive layout hints were not visible in sampled HTML; external CSS may still handle them")

    return MobileViewportSignal(score, issues, positives)


async def check_pagespeed(context: AuditContext) -> CheckResult:
    """Measure included structural performance proxy signals."""
    if not context.homepage.ok:
        finding, fix = _TEMPLATES["fetch_error"]
        return CheckResult(
            pillar="seo",
            check_name="pagespeed",
            label="HTML Performance Proxies",
            state="warn",
            evidence_level="unknown",
            score=0,
            max_score=3,
            finding=finding,
            fix=fix,
            effort="medium",
        )

    soup = BeautifulSoup(context.homepage.text, "lxml")
    head = soup.find("head")

    blocking_stylesheets = _count_render_blocking_stylesheets(head)
    unsized_media = _count_unsized_media(soup)
    sync_scripts = _count_sync_head_scripts(head)
    mobile_signal = _mobile_viewport_signal(soup)

    response_score = 4 if context.homepage.elapsed_ms <= _RESPONSE_GOOD else (
        2 if context.homepage.elapsed_ms <= _RESPONSE_POOR else 0
    )
    validator_score = 4 if _has_recrawl_validator(context.homepage.headers) else (
        2 if _has_cache_policy(context.homepage.headers) else 0
    )
    lcp_score = _score_signal(blocking_stylesheets, 1, 3)
    cls_score = _score_signal(unsized_media, 0, 3)
    inp_score = _score_signal(sync_scripts, 1, 3)
    size_score = 4 if _body_size_ok(context.homepage.headers, context.homepage.text) else 0

    raw_score = (
        response_score
        + validator_score
        + lcp_score
        + cls_score
        + inp_score
        + size_score
        + mobile_signal.score
    )
    total_score = round(raw_score / 28 * 3)

    issues: list[str] = []
    if response_score < 4:
        issues.append(f"slow response ({context.homepage.elapsed_ms}ms, target <{_RESPONSE_GOOD}ms)")
    if validator_score < 4:
        issues.append("missing ETag or Last-Modified cache validator for efficient recrawls")
    if lcp_score < 4:
        issues.append(f"{blocking_stylesheets} render-blocking stylesheet(s) in <head>")
    if cls_score < 4:
        issues.append(f"{unsized_media} image(s)/video(s) missing explicit dimensions")
    if inp_score < 4:
        issues.append(f"{sync_scripts} synchronous script(s) in <head>")
    if size_score < 4:
        issues.append("HTML response is larger than the first 15MB crawled by common search bots")
    issues.extend(mobile_signal.issues)

    if not issues:
        state = "pass"
        finding, fix = _TEMPLATES["pass"]
        finding = finding.format(elapsed=context.homepage.elapsed_ms, mobile_detail=" ".join(mobile_signal.positives))
    elif total_score > 0:
        state = "partial"
        finding, fix = _TEMPLATES["partial"]
        finding = finding.format(issues="; ".join(issues))
    else:
        state = "warn"
        finding, fix = _TEMPLATES["weak_proxy"]
        finding = finding.format(issues="; ".join(issues))

    return CheckResult(
        pillar="seo",
        check_name="pagespeed",
        label="Crawl Efficiency & HTML Performance",
        state=state,
        evidence_level="inferred",
        score=total_score,
        max_score=3,
        finding=finding,
        fix=fix,
        effort="medium",
    )
