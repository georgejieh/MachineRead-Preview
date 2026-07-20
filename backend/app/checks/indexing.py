import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from app.audit_context import AuditContext
from app.crawler_registry import active_crawler_caveats, crawler_directive_sources
from app.models import CheckResult

_BLOCKING_DIRECTIVES = {"noindex", "none"}
_RESTRICTIVE_DIRECTIVES = {"nofollow", "noarchive", "notranslate"}
_SNIPPET_RESTRICTIVE_DIRECTIVES = {"nosnippet", "noimageindex"}
_KNOWN_CRAWLER_DIRECTIVE_SOURCES = crawler_directive_sources()
_IMAGE_PREVIEW_RE = re.compile(r"^max-image-preview:(none|standard|large)$")
_MAX_SNIPPET_ZERO_RE = re.compile(r"^max-snippet:0$")
_MAX_VIDEO_ZERO_RE = re.compile(r"^max-video-preview:0$")


@dataclass(frozen=True)
class DirectiveSource:
    label: str
    directives: set[str]
    crawler: str | None = None


@dataclass(frozen=True)
class ImagePreviewSignals:
    none: list[str]
    standard: list[str]
    large: list[str]


@dataclass(frozen=True)
class IndexingSignals:
    blocking: list[str]
    restrictive: list[str]
    snippet_restrictive: list[str]
    unavailable_after: list[str]
    data_nosnippet_count: int
    image_preview: ImagePreviewSignals
    crawler_specific: set[str]


def _split_directives(value: str) -> set[str]:
    return set(_split_directive_list(value))


def _split_directive_list(value: str) -> list[str]:
    directives: list[str] = []
    for part in value.lower().split(","):
        cleaned = _normalise_directive(part)
        if cleaned:
            directives.append(cleaned)
    return directives


def _has_zero_snippet_limit(value: str) -> bool:
    directives = _split_directives(value)
    return any(_directive_blocks_snippet_previews(_directive_body(directive)) for directive in directives)


def _normalise_directive(value: str) -> str:
    cleaned = " ".join(value.strip().lower().split())
    return re.sub(r"\s*:\s*", ":", cleaned)


def _directive_blocks_snippet_previews(directive: str) -> bool:
    return (
        bool(_MAX_SNIPPET_ZERO_RE.fullmatch(directive))
        or directive == "max-image-preview:none"
        or bool(_MAX_VIDEO_ZERO_RE.fullmatch(directive))
    )


def _directive_body(directive: str) -> str:
    prefix, separator, scoped_directive = directive.partition(":")
    if separator and prefix in _KNOWN_CRAWLER_DIRECTIVE_SOURCES and scoped_directive:
        return scoped_directive
    return directive


def _meta_robot_directives(soup: BeautifulSoup) -> list[DirectiveSource]:
    sources: list[DirectiveSource] = []
    for tag in soup.find_all("meta"):
        name = tag.get("name", "").strip().lower()
        if name == "robots":
            sources.append(DirectiveSource("meta robots", _split_directives(tag.get("content", ""))))
        elif name in _KNOWN_CRAWLER_DIRECTIVE_SOURCES:
            sources.append(
                DirectiveSource(
                    f"meta {name}",
                    _split_directives(tag.get("content", "")),
                    crawler=name,
                )
            )
    return [source for source in sources if source.directives]


def _x_robots_directives(value: str) -> list[DirectiveSource]:
    global_directives: set[str] = set()
    crawler_directives: dict[str, set[str]] = {}
    for directive in _split_directive_list(value):
        prefix, separator, scoped_directive = directive.partition(":")
        if separator and prefix in _KNOWN_CRAWLER_DIRECTIVE_SOURCES and scoped_directive:
            crawler_directives.setdefault(prefix, set()).add(scoped_directive)
        else:
            global_directives.add(directive)

    sources: list[DirectiveSource] = []
    if global_directives:
        sources.append(DirectiveSource("X-Robots-Tag", global_directives))
    for crawler, directives in sorted(crawler_directives.items()):
        sources.append(DirectiveSource(f"X-Robots-Tag {crawler}", directives, crawler=crawler))
    return sources


def _data_nosnippet_count(soup: BeautifulSoup) -> int:
    return len(soup.find_all(attrs={"data-nosnippet": True}))


def _image_preview_signals(sources: list[DirectiveSource]) -> ImagePreviewSignals:
    none: list[str] = []
    standard: list[str] = []
    large: list[str] = []
    for source in sources:
        for directive in source.directives:
            match = _IMAGE_PREVIEW_RE.fullmatch(directive)
            if not match:
                continue
            value = match.group(1)
            if value == "none":
                none.append(source.label)
            elif value == "standard":
                standard.append(source.label)
            else:
                large.append(source.label)
    return ImagePreviewSignals(none=none, standard=standard, large=large)


def _indexing_signals(soup: BeautifulSoup, headers: dict[str, str]) -> IndexingSignals:
    sources = _meta_robot_directives(soup)
    x_robots = headers.get("x-robots-tag", "")
    if x_robots:
        sources.extend(_x_robots_directives(x_robots))

    blocking = [
        source.label
        for source in sources
        if source.directives & _BLOCKING_DIRECTIVES
    ]
    restrictive = [
        source.label
        for source in sources
        if source.directives & _RESTRICTIVE_DIRECTIVES
    ]
    snippet_restrictive = [
        source.label
        for source in sources
        if source.directives & _SNIPPET_RESTRICTIVE_DIRECTIVES
        or any(_directive_blocks_snippet_previews(directive) for directive in source.directives)
    ]
    unavailable_after = [
        source.label
        for source in sources
        if any(value.startswith("unavailable_after:") for value in source.directives)
    ]

    return IndexingSignals(
        blocking=blocking,
        restrictive=restrictive,
        snippet_restrictive=snippet_restrictive,
        unavailable_after=unavailable_after,
        data_nosnippet_count=_data_nosnippet_count(soup),
        image_preview=_image_preview_signals(sources),
        crawler_specific={source.crawler for source in sources if source.crawler},
    )


def _crawler_directive_caveat(crawler_tokens: set[str]) -> str:
    caveats = active_crawler_caveats(
        "directive",
        crawler_tokens=crawler_tokens,
    )
    return " Crawler-specific caveat: " + " ".join(caveats) if caveats else ""


async def check_indexing(context: AuditContext) -> CheckResult:
    """Check HTTP and HTML indexing directives."""
    if not context.homepage.ok:
        return CheckResult(
            pillar="seo",
            check_name="indexing",
            label="Indexing Directives",
            state="warn",
            evidence_level="unknown",
            score=0,
            max_score=5,
            finding="Could not fetch homepage to inspect indexing directives.",
            fix="Ensure the homepage is publicly accessible and returns HTML.",
            effort="low",
        )

    soup = BeautifulSoup(context.homepage.text, "lxml")
    signals = _indexing_signals(soup, context.homepage.headers)

    if signals.blocking:
        state = "fail"
        score = 0
        finding = "Homepage contains blocking indexing directives in: " + ", ".join(signals.blocking) + "."
        fix = "Remove noindex/none directives from public pages that should appear in search and AI retrieval."
    elif signals.snippet_restrictive or signals.data_nosnippet_count:
        state = "partial"
        score = 2
        snippet_sources = signals.snippet_restrictive.copy()
        if signals.data_nosnippet_count:
            snippet_sources.append(f"{signals.data_nosnippet_count} data-nosnippet section(s)")
        finding = (
            "Homepage allows indexing but restricts snippets, images, or previews in: "
            + ", ".join(snippet_sources)
            + "."
        )
        fix = (
            "Review nosnippet, data-nosnippet, max-snippet:0, max-image-preview:none, "
            "noimageindex, and max-video-preview restrictions on pages that should be "
            "useful in search results and AI retrieval."
        )
    elif signals.image_preview.standard:
        state = "partial"
        score = 4
        finding = (
            "Homepage allows indexing but limits large image previews in: "
            + ", ".join(signals.image_preview.standard)
            + "."
        )
        fix = (
            "Use max-image-preview:large on public pages where rich image previews are "
            "appropriate. Keep any stricter preview policy only when it is intentional."
        )
    elif signals.restrictive or signals.unavailable_after:
        state = "partial"
        score = 3
        restricted_sources = signals.restrictive + signals.unavailable_after
        finding = "Homepage contains restrictive crawler directives in: " + ", ".join(restricted_sources) + "."
        fix = (
            "Review snippet and follow restrictions. They may reduce how search engines "
            "and AI retrieval systems quote or traverse the page."
        )
    else:
        state = "pass"
        score = 5
        finding = "No blocking or restrictive indexing directives were found on the homepage."
        if signals.image_preview.large:
            finding += " Large image previews are allowed in: " + ", ".join(signals.image_preview.large) + "."
        fix = "No action needed."

    finding += _crawler_directive_caveat(signals.crawler_specific)

    return CheckResult(
        pillar="seo",
        check_name="indexing",
        label="Indexing Directives",
        state=state,
        score=score,
        max_score=5,
        finding=finding,
        fix=fix,
        effort="low",
    )
