from dataclasses import dataclass
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

from app.audit_context import AuditContext
from app.models import CheckResult

if TYPE_CHECKING:
    from app.fetch_evidence import FetchEvidence

_WORD_COUNT_THRESHOLD = 50
_MAIN_CONTENT_WORD_THRESHOLD = 80
_HIGH_BOILERPLATE_RATIO = 0.65
_HIGH_NAVIGATION_RATIO = 0.45
_HIGH_SCRIPT_STYLE_RATIO = 0.50
_HIGH_HTML_TO_TEXT_RATIO = 40.0
_IGNORED_TEXT_TAGS = ["script", "style", "head", "noscript", "template", "svg"]
_BOILERPLATE_SELECTOR = "nav, header, footer, aside"
_MAIN_CONTENT_SELECTOR = 'main, article, [role="main"]'


@dataclass(frozen=True)
class ExtractionEfficiencySignal:
    visible_words: int
    main_content_words: int
    boilerplate_ratio: float
    navigation_ratio: float
    script_style_ratio: float
    html_to_text_ratio: float
    issues: list[str]
    positives: list[str]


_TEMPLATES = {
    "readable": (
        "Page returns substantial content ({word_count} words) on a raw HTTP fetch "
        "with no JavaScript execution.",
        "No action needed. Your key content is available in raw HTML.",
    ),
    "thin": (
        "Page returns only {word_count} words on a raw fetch, but it does not look "
        "like a JavaScript app shell. This is thin HTML, not necessarily JS-only rendering.",
        "Add more useful page text, structured content, or links in raw HTML so "
        "agents have enough context to extract.",
    ),
    "probable_js_shell": (
        "Page returns only {word_count} words and has JavaScript app-shell signals. "
        "Important content is likely rendered after JavaScript execution.",
        "Use server-side rendering, static generation, or an HTML fallback for key "
        "pages. Agents should not need browser automation to read core content.",
    ),
    "blocked_or_empty": (
        "Raw fetch returned no meaningful body text.",
        "Ensure the homepage returns public HTML to non-browser clients and does "
        "not require a challenge page before content is served.",
    ),
    "fetch_error": (
        "Could not fetch homepage to check server-side rendering.",
        "Ensure the homepage is publicly accessible.",
    ),
}


def _byte_len(value: str) -> int:
    return len(value.encode("utf-8", errors="ignore"))


def _format_percent(value: float) -> str:
    return f"{round(value * 100)}%"


def _format_html_to_text_ratio(value: float) -> str:
    return f"{round(value)}:1"


def _cleaned_text(markup: object) -> str:
    soup = BeautifulSoup(str(markup), "lxml")
    for tag in soup(_IGNORED_TEXT_TAGS):
        tag.decompose()
    return " ".join(soup.get_text(separator=" ").split())


def _text_word_count(markup: object) -> int:
    text = _cleaned_text(markup)
    return len(text.split()) if text else 0


def _selector_word_count(soup: BeautifulSoup, selector: str) -> int:
    clone = BeautifulSoup(str(soup), "lxml")
    words = 0
    for tag in clone.select(selector):
        if tag.parent is None:
            continue
        words += _text_word_count(tag)
        tag.decompose()
    return words


def _content_words_without_boilerplate(soup: BeautifulSoup) -> int:
    clone = BeautifulSoup(str(soup.body or soup), "lxml")
    for tag in clone.select(_BOILERPLATE_SELECTOR):
        tag.decompose()
    return _text_word_count(clone)


def _main_content_words(soup: BeautifulSoup) -> int:
    explicit_candidates = soup.select(_MAIN_CONTENT_SELECTOR)
    if explicit_candidates:
        return max(_text_word_count(candidate) for candidate in explicit_candidates)
    return _content_words_without_boilerplate(soup)


def _script_style_ratio(soup: BeautifulSoup, html_bytes: int) -> float:
    if html_bytes <= 0:
        return 0
    script_style_bytes = sum(_byte_len(str(tag)) for tag in soup.find_all(["script", "style"]))
    return script_style_bytes / html_bytes


def _extraction_efficiency(html: str) -> ExtractionEfficiencySignal:
    soup = BeautifulSoup(html, "lxml")
    visible_text = _cleaned_text(soup)
    visible_words = len(visible_text.split()) if visible_text else 0
    visible_text_bytes = _byte_len(visible_text)
    html_bytes = _byte_len(html)

    main_content_words = min(_main_content_words(soup), visible_words)
    boilerplate_ratio = (
        (visible_words - main_content_words) / visible_words if visible_words else 1.0
    )
    navigation_ratio = (
        _selector_word_count(soup, _BOILERPLATE_SELECTOR) / visible_words
        if visible_words
        else 1.0
    )
    script_style_ratio = _script_style_ratio(soup, html_bytes)
    html_to_text_ratio = html_bytes / max(visible_text_bytes, 1)

    issues: list[str] = []
    positives: list[str] = []
    if visible_words == 0:
        issues.append("no visible raw text for extraction")
    elif main_content_words < _MAIN_CONTENT_WORD_THRESHOLD:
        issues.append(f"main-content area has only {main_content_words} words")
    else:
        positives.append(f"main-content area exposes {main_content_words} words")

    if boilerplate_ratio > _HIGH_BOILERPLATE_RATIO:
        issues.append(f"high boilerplate ratio ({_format_percent(boilerplate_ratio)} outside main content)")
    elif visible_words:
        positives.append(f"boilerplate ratio is {_format_percent(boilerplate_ratio)}")

    if navigation_ratio > _HIGH_NAVIGATION_RATIO:
        issues.append(f"navigation-heavy page ({_format_percent(navigation_ratio)} of visible text)")
    elif visible_words:
        positives.append(f"navigation/chrome text is {_format_percent(navigation_ratio)} of visible text")

    if script_style_ratio > _HIGH_SCRIPT_STYLE_RATIO:
        issues.append(f"script/style markup is heavy ({_format_percent(script_style_ratio)} of raw HTML)")
    else:
        positives.append(f"script/style markup is {_format_percent(script_style_ratio)} of raw HTML")

    if html_to_text_ratio > _HIGH_HTML_TO_TEXT_RATIO:
        issues.append(f"high HTML-to-text ratio ({_format_html_to_text_ratio(html_to_text_ratio)})")
    else:
        positives.append(f"HTML-to-text ratio is {_format_html_to_text_ratio(html_to_text_ratio)}")

    return ExtractionEfficiencySignal(
        visible_words=visible_words,
        main_content_words=main_content_words,
        boilerplate_ratio=boilerplate_ratio,
        navigation_ratio=navigation_ratio,
        script_style_ratio=script_style_ratio,
        html_to_text_ratio=html_to_text_ratio,
        issues=issues,
        positives=positives,
    )


def _efficiency_finding(signal: ExtractionEfficiencySignal) -> str:
    metrics = (
        f"main-content words {signal.main_content_words}, "
        f"boilerplate {_format_percent(signal.boilerplate_ratio)}, "
        f"navigation/chrome {_format_percent(signal.navigation_ratio)}, "
        f"script/style {_format_percent(signal.script_style_ratio)}, "
        f"HTML-to-text {_format_html_to_text_ratio(signal.html_to_text_ratio)}"
    )
    if signal.issues:
        return " Extraction proxy flags: " + "; ".join(signal.issues) + f". Metrics: {metrics}."
    return f" Extraction proxy looks efficient: {metrics}."


def _efficiency_fix(signal: ExtractionEfficiencySignal) -> str:
    if not signal.issues:
        return ""
    return (
        " Strengthen raw HTML extraction by placing substantial page copy in a "
        "main/article content area, reducing repeated navigation or chrome text, "
        "and keeping script/style payloads proportionate to visible content."
    )


def _has_app_shell_signals(soup: BeautifulSoup) -> bool:
    # Note: marker uses "/@vite/" (the dev-server import path Vite injects into
    # every <script type="module"> on a Vite dev page). The bare substring "vite"
    # would also match common copy text like "invite" or "activate", so we look
    # for the injected path instead. script_count threshold is 5 because
    # 3-4 scripts is normal for analytics/ads/chat widgets on non-SPA sites.
    script_count = len(soup.find_all("script", src=True))
    app_roots = soup.find_all(id=lambda value: value in {"root", "app", "__next", "__nuxt", "svelte"})
    hydration_markers = any(
        marker in str(soup)[:20000]
        for marker in ("__NEXT_DATA__", "data-reactroot", "ng-version", "/@vite/", "__NUXT__")
    )
    return script_count >= 5 or bool(app_roots) or hydration_markers


def _word_count(html: str) -> int:
    return _text_word_count(html)


def _analyse_rendering(html: str) -> tuple[str, int]:
    soup = BeautifulSoup(html, "lxml")
    words = _word_count(html)
    has_app_shell = _has_app_shell_signals(soup)
    if words < _WORD_COUNT_THRESHOLD and has_app_shell:
        return "probable_js_shell", words
    if words == 0:
        return "blocked_or_empty", words
    if words >= _WORD_COUNT_THRESHOLD:
        return "readable", words
    return "thin", words


async def check_ssr(
    context: AuditContext,
    qa2_evidence: "FetchEvidence | None" = None,
) -> CheckResult:
    """Detect whether the page serves readable content without JavaScript."""
    if not context.homepage.ok:
        finding, fix = _TEMPLATES["fetch_error"]
        return CheckResult(
            pillar="scrapability",
            check_name="ssr",
            label="Raw HTML Readability",
            state="warn",
            evidence_level="unknown",
            score=0,
            max_score=4,
            finding=finding,
            fix=fix,
            effort="high",
        )

    result, words = _analyse_rendering(context.homepage.text)
    efficiency = _extraction_efficiency(context.homepage.text)
    finding, fix = _TEMPLATES[result]
    finding = finding.format(word_count=words) if "{word_count}" in finding else finding
    finding += _efficiency_finding(efficiency)
    fix += _efficiency_fix(efficiency)

    if result == "readable":
        if efficiency.issues:
            state = "partial"
            score = 2 if len(efficiency.issues) >= 3 else 3
            evidence = "inferred"
        else:
            state, score, evidence = "pass", 4, "verified"
    elif result == "thin":
        state = "partial"
        score = 1 if len(efficiency.issues) >= 3 else 2
        evidence = "inferred"
    else:
        state, score, evidence = "fail", 0, "inferred"

    if (
        state == "pass"
        and qa2_evidence is not None
        and qa2_evidence.extraction_readiness.hidden_node_count > 0
        and qa2_evidence.extraction_readiness.app_shell_risk
    ):
        extraction = qa2_evidence.extraction_readiness
        state, score, evidence = "partial", 3, "inferred"
        finding += (
            " Extraction-readiness proxy excluded "
            f"{extraction.hidden_node_count} conservatively hidden source node(s); "
            "the remaining source has severe JavaScript app-shell risk."
        )
        fix = (
            "Expose the primary readable content outside hidden source containers and "
            "keep a substantial server-rendered fallback for app-shell clients."
        )

    return CheckResult(
        pillar="scrapability",
        check_name="ssr",
        label="Raw HTML Readability",
        state=state,
        evidence_level=evidence,
        score=score,
        max_score=4,
        finding=finding,
        fix=fix,
        effort="high",
    )
