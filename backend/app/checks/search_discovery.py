import asyncio
import json
import re
import urllib.robotparser
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from app.audit_context import AuditContext
from app.checks.llms_txt import analyse_markdown_response
from app.checks.schema_ld import _extract_schemas
from app.checks.sitemap_analysis import SitemapEntry, collect_sitemap_sample, sitemap_urls_from_robots
from app.fetching import FetchResult, fetch_url, make_root_url
from app.models import CheckResult

if TYPE_CHECKING:
    from app.qa2_evidence import QA2EvidenceBundle

_SEARCH_BOTS = ["Googlebot", "Bingbot"]
_COMMON_FEED_PATHS = ("/feed.xml", "/rss.xml", "/atom.xml", "/feed.json")
_FEED_ACCEPT = (
    "application/rss+xml, application/atom+xml, application/feed+json, "
    "application/json;q=0.9, application/xml;q=0.8, text/xml;q=0.8,*/*;q=0.5"
)
_FEED_SAMPLE_LIMIT = 4
_FEED_STALE_AFTER_DAYS = 180
_MARKDOWN_ACCEPT = "text/markdown, text/plain;q=0.9, text/html;q=0.4,*/*;q=0.1"
_MARKDOWN_TYPES = {"text/markdown", "text/x-markdown", "text/plain"}
_SAMPLE_PAGE_LIMIT = 5
_HREFLANG_SAMPLE_LIMIT = 5
_HREFLANG_RECIPROCAL_LIMIT = 3
_MIN_SAMPLED_PAGE_WORDS = 40
_MAX_SAMPLED_HTML_BYTES = 15 * 1024 * 1024
_WORD_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9'_-]*")
_HREFLANG_PATTERN = re.compile(
    r"^[a-z]{2,3}(?:-[a-z]{4})?(?:-(?:[a-z]{2}|[0-9]{3}))?$",
    re.IGNORECASE,
)
_FRESH_SECTION_TERMS = (
    "blog",
    "news",
    "resources",
    "updates",
    "changelog",
    "articles",
    "insights",
)
_FEED_LINK_TYPES = {
    "application/rss+xml",
    "application/atom+xml",
    "application/feed+json",
    "application/json",
}
_FEED_DATE_FIELDS = {
    "pubdate",
    "published",
    "updated",
    "modified",
    "date",
    "lastbuilddate",
}
_INDEX_BLOCKERS = {"noindex", "none"}
_TRUST_SURFACE_ORDER = ("about", "contact", "privacy", "terms", "support", "returns", "shipping")
_CORE_TRUST_SURFACES = ("about", "contact", "privacy")
_RECOMMENDED_TRUST_SURFACES = ("terms", "support")
_COMMERCE_TRUST_SURFACES = ("returns", "shipping")
_TRUST_SURFACE_LABELS = {
    "about": "About",
    "contact": "Contact",
    "privacy": "Privacy",
    "terms": "Terms",
    "support": "Support",
    "returns": "Returns",
    "shipping": "Shipping",
}
_TRUST_SURFACE_PATTERNS = {
    "about": re.compile(r"\b(about|company|mission|team|our story|who we are)\b"),
    "contact": re.compile(r"\b(contact|contact us|get in touch|locations?)\b"),
    "privacy": re.compile(r"\bprivacy\b"),
    "terms": re.compile(r"\b(terms|terms of service|terms and conditions|legal)\b"),
    "support": re.compile(r"\b(support|help center|customer service|faq|faqs)\b"),
    "returns": re.compile(r"\b(returns?|return policy|refunds?|exchanges?)\b"),
    "shipping": re.compile(r"\b(shipping|delivery|fulfillment)\b"),
}


@dataclass(frozen=True)
class SamplePageMetadataResult:
    ok: bool
    positives: list[str]
    issues: list[str]
    caveats: list[str]


@dataclass(frozen=True)
class HreflangLink:
    language: str
    href: str
    absolute_url: str
    is_absolute: bool


@dataclass(frozen=True)
class HreflangValidationResult:
    present: bool
    positives: list[str]
    issues: list[str]
    caveats: list[str]


@dataclass(frozen=True)
class TrustSurfaceResult:
    found: dict[str, list[str]]
    positives: list[str]
    issues: list[str]
    caveats: list[str]


@dataclass(frozen=True)
class FeedParseResult:
    url: str
    feed_type: str
    item_count: int
    dated_item_count: int
    latest_date: date | None
    invalid_date_count: int
    parse_error: str | None = None


@dataclass(frozen=True)
class FeedFreshnessResult:
    available: bool
    positives: list[str]
    issues: list[str]
    caveats: list[str]


def _search_bots_allowed(robots_text: str, url: str) -> tuple[bool, list[str]]:
    if not robots_text.strip():
        return True, []

    parser = urllib.robotparser.RobotFileParser()
    parser.parse(robots_text.splitlines())
    blocked = [bot for bot in _SEARCH_BOTS if not parser.can_fetch(bot, url)]
    return not blocked, blocked


def _split_directives(value: str) -> set[str]:
    return {part.strip().lower() for part in value.split(",") if part.strip()}


def _page_is_indexable(response: FetchResult) -> bool:
    x_robots = response.headers.get("x-robots-tag", "")
    if _split_directives(x_robots) & _INDEX_BLOCKERS:
        return False

    soup = BeautifulSoup(response.text, "lxml")
    for tag in soup.find_all("meta"):
        name = tag.get("name", "").lower()
        if name in {"robots", "googlebot", "bingbot"}:
            if _split_directives(tag.get("content", "")) & _INDEX_BLOCKERS:
                return False
    return True


def _page_robots_directives(response: FetchResult, soup: BeautifulSoup) -> set[str]:
    directives = _split_directives(response.headers.get("x-robots-tag", ""))
    for tag in soup.find_all("meta"):
        name = tag.get("name", "").lower()
        if name in {"robots", "googlebot", "bingbot"}:
            directives.update(_split_directives(tag.get("content", "")))
    return directives


def _same_site(url: str, root_url: str) -> bool:
    root_hostname = urlparse(root_url).hostname
    hostname = urlparse(url).hostname
    if not root_hostname or not hostname:
        return False
    return hostname.removeprefix("www.") == root_hostname.removeprefix("www.")


def _normalise_url_for_comparison(url: str) -> str:
    return url.rstrip("/")


def _is_absolute_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _valid_hreflang_value(value: str) -> bool:
    return value == "x-default" or bool(_HREFLANG_PATTERN.fullmatch(value))


def _trust_candidate_text(url: str, label: str) -> str:
    parsed = urlparse(url)
    path_text = unquote(" ".join(part for part in parsed.path.split("/") if part))
    raw = f"{path_text} {label}"
    return re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()


def _candidate_trust_surfaces(url: str, label: str) -> list[str]:
    candidate_text = _trust_candidate_text(url, label)
    return [
        surface
        for surface in _TRUST_SURFACE_ORDER
        if _TRUST_SURFACE_PATTERNS[surface].search(candidate_text)
    ]


def _append_surface_source(found: dict[str, list[str]], surface: str, source: str) -> None:
    sources = found.setdefault(surface, [])
    if source not in sources:
        sources.append(source)


def _surface_labels(surfaces: tuple[str, ...] | list[str]) -> str:
    return ", ".join(_TRUST_SURFACE_LABELS[surface] for surface in surfaces)


def _trust_summary(found: dict[str, list[str]]) -> str:
    visible = []
    for surface in _TRUST_SURFACE_ORDER:
        sources = found.get(surface)
        if sources:
            visible.append(f"{_TRUST_SURFACE_LABELS[surface]} via {'/'.join(sources)}")
    return ", ".join(visible)


def _trust_surface_discovery(
    context: AuditContext,
    entries: list[SitemapEntry],
    include_ecommerce: bool = False,
) -> TrustSurfaceResult:
    found: dict[str, list[str]] = {}

    if context.homepage.ok:
        soup = BeautifulSoup(context.homepage.text, "lxml")
        for link in soup.find_all("a", href=True):
            absolute_url = urljoin(context.homepage.final_url, link.get("href", ""))
            if not _is_absolute_http_url(absolute_url) or not _same_site(absolute_url, context.url):
                continue
            label = " ".join(
                part
                for part in (
                    link.get_text(" ", strip=True),
                    link.get("aria-label", ""),
                    link.get("title", ""),
                )
                if part
            )
            for surface in _candidate_trust_surfaces(absolute_url, label):
                _append_surface_source(found, surface, "homepage")

    seen_sitemap_urls: set[str] = set()
    for entry in entries:
        if entry.loc in seen_sitemap_urls or not _same_site(entry.loc, context.url):
            continue
        seen_sitemap_urls.add(entry.loc)
        for surface in _candidate_trust_surfaces(entry.loc, ""):
            _append_surface_source(found, surface, "sitemap")

    positives: list[str] = []
    issues: list[str] = []
    caveats: list[str] = []

    if found:
        positives.append("trust/entity surfaces discovered: " + _trust_summary(found))

    missing_core = [surface for surface in _CORE_TRUST_SURFACES if surface not in found]
    if missing_core:
        issues.append("missing core trust/entity pages: " + _surface_labels(missing_core))
    else:
        positives.append("core About, Contact, and Privacy surfaces are discoverable")

    missing_recommended = [surface for surface in _RECOMMENDED_TRUST_SURFACES if surface not in found]
    if missing_recommended:
        caveats.append("recommended trust/support pages were not discovered: " + _surface_labels(missing_recommended))

    if include_ecommerce:
        missing_commerce = [surface for surface in _COMMERCE_TRUST_SURFACES if surface not in found]
        if missing_commerce:
            issues.append("commerce scope missing policy pages: " + _surface_labels(missing_commerce))
        else:
            positives.append("commerce Returns and Shipping surfaces are discoverable")
    elif any(surface in found for surface in _COMMERCE_TRUST_SURFACES):
        positives.append("commerce policy surfaces are discoverable even though commerce scope is off")

    return TrustSurfaceResult(found=found, positives=positives, issues=issues, caveats=caveats)


def _rel_values(tag: object) -> set[str]:
    raw_rel = getattr(tag, "get", lambda *_: [])("rel", [])
    if isinstance(raw_rel, str):
        return {part.lower() for part in raw_rel.split()}
    return {str(part).lower() for part in raw_rel}


def _hreflang_links(response: FetchResult, soup: BeautifulSoup) -> list[HreflangLink]:
    links: list[HreflangLink] = []
    for tag in soup.find_all("link"):
        if "alternate" not in _rel_values(tag) or not tag.has_attr("hreflang"):
            continue
        href = tag.get("href", "").strip()
        language = tag.get("hreflang", "").strip().lower()
        links.append(
            HreflangLink(
                language=language,
                href=href,
                absolute_url=urljoin(response.final_url, href) if href else "",
                is_absolute=_is_absolute_http_url(href),
            )
        )
    return links


def _has_title(soup: BeautifulSoup) -> bool:
    return bool(soup.title and soup.title.get_text(" ", strip=True))


def _canonical_url(response: FetchResult, soup: BeautifulSoup) -> str | None:
    canonical = soup.find("link", attrs={"rel": "canonical"})
    href = canonical.get("href", "") if canonical else ""
    return urljoin(response.final_url, href) if href else None


def _sample_page_word_count(soup: BeautifulSoup) -> int:
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return len(_WORD_PATTERN.findall(soup.get_text(" ", strip=True)))


def _markdown_alternate_url(response: FetchResult, soup: BeautifulSoup) -> str | None:
    for tag in soup.find_all("link", rel=lambda value: value and "alternate" in value):
        href = tag.get("href")
        if not href:
            continue
        link_type = tag.get("type", "").lower()
        lowered_href = href.lower()
        if link_type in _MARKDOWN_TYPES or lowered_href.endswith((".md", ".markdown", ".txt")):
            return urljoin(response.final_url, href)
    return None


def _index_md_url(response: FetchResult) -> str:
    return response.final_url.rstrip("/") + "/index.md"


def _markdown_preserves_schema_hint(markdown_text: str) -> bool:
    lowered = markdown_text[:6000].lower()
    return any(token in lowered for token in ("json-ld", "schema.org", "@type", "structured data"))


async def _sample_markdown_caveats(
    page_results: list[tuple[FetchResult, BeautifulSoup, bool]],
    markdown_by_page: dict[str, FetchResult] | None = None,
) -> tuple[list[str], list[str]]:
    candidates: list[tuple[str, bool]] = []
    for response, soup, has_schema in page_results:
        alternate_url = _markdown_alternate_url(response, soup)
        if alternate_url:
            candidates.append((alternate_url, True))
        else:
            candidates.append((_index_md_url(response), False))

    if markdown_by_page is None:
        responses = list(
            await asyncio.gather(
                *[fetch_url(url, accept=_MARKDOWN_ACCEPT) for url, _ in candidates],
                return_exceptions=False,
            )
        )
    else:
        responses = [markdown_by_page[response.final_url] for response, _, _ in page_results]
    usable = 0
    invalid_linked = 0
    schema_not_preserved = 0
    for (_, linked), markdown_response, (_, _, has_schema) in zip(candidates, responses, page_results, strict=True):
        result = analyse_markdown_response(markdown_response, "sampled page Markdown alternate")
        if result.available:
            usable += 1
            if has_schema and not _markdown_preserves_schema_hint(markdown_response.text):
                schema_not_preserved += 1
        elif linked:
            invalid_linked += 1

    positives: list[str] = []
    caveats: list[str] = []
    if usable:
        positives.append(f"{usable} sampled page Markdown alternate or index.md export(s) are usable")
    else:
        caveats.append("no sampled page Markdown alternate or index.md export was found")
    if invalid_linked:
        caveats.append(f"{invalid_linked} linked sampled page Markdown alternate(s) were not usable")
    if schema_not_preserved:
        caveats.append(
            f"{schema_not_preserved} sampled Markdown export(s) do not appear to preserve JSON-LD hints"
        )
    return positives, caveats


async def _sample_pages_have_discovery_metadata(
    context: AuditContext,
    entries: list[SitemapEntry],
    responses: list[FetchResult] | None = None,
    markdown_by_page: dict[str, FetchResult] | None = None,
) -> SamplePageMetadataResult:
    sampled = entries[:_SAMPLE_PAGE_LIMIT]
    if not sampled:
        return SamplePageMetadataResult(False, [], ["no sitemap URLs were available to sample"], [])

    responses = responses or await _fetch_all([entry.loc for entry in sampled])
    failed = [entry.loc for entry, response in zip(sampled, responses, strict=True) if not response.ok]
    page_results: list[tuple[FetchResult, BeautifulSoup, bool]] = []
    blocked = [
        entry.loc
        for entry, response in zip(sampled, responses, strict=True)
        if response.ok and not _page_is_indexable(response)
    ]
    offsite_canonicals = []
    for entry, response in zip(sampled, responses, strict=True):
        if not response.ok:
            continue
        soup = BeautifulSoup(response.text, "lxml")
        canonical_url = _canonical_url(response, soup)
        if canonical_url and not _same_site(canonical_url, context.url):
            offsite_canonicals.append(entry.loc)
        schemas, _ = _extract_schemas(soup)
        page_results.append((response, soup, bool(schemas)))

    accessible_count = len(page_results)
    missing_title = [response.final_url for response, soup, _ in page_results if not _has_title(soup)]
    missing_canonical = [response.final_url for response, soup, _ in page_results if not _canonical_url(response, soup)]
    missing_schema = [response.final_url for response, _, has_schema in page_results if not has_schema]
    invalid_schema_pages = []
    thin_pages = []
    oversized_pages = []
    explicit_robots_pages = []
    for response, soup, _ in page_results:
        _, invalid_schema_count = _extract_schemas(soup)
        if invalid_schema_count:
            invalid_schema_pages.append(response.final_url)
        word_count = _sample_page_word_count(BeautifulSoup(response.text, "lxml"))
        html_size = len(response.text.encode("utf-8"))
        if word_count < _MIN_SAMPLED_PAGE_WORDS:
            thin_pages.append(response.final_url)
        if html_size > _MAX_SAMPLED_HTML_BYTES:
            oversized_pages.append(response.final_url)
        directives = _page_robots_directives(response, soup)
        if directives:
            explicit_robots_pages.append(response.final_url)

    issues: list[str] = []
    if failed:
        issues.append(f"{len(failed)} sampled sitemap URL(s) did not return accessible pages")
    if blocked:
        issues.append(f"{len(blocked)} sampled sitemap URL(s) are noindex/none")
    if offsite_canonicals:
        issues.append(f"{len(offsite_canonicals)} sampled sitemap URL(s) canonicalize off-site")
    if missing_title:
        issues.append(f"{len(missing_title)} sampled page(s) are missing a title")
    if missing_canonical:
        issues.append(f"{len(missing_canonical)} sampled page(s) are missing a canonical link")
    if not page_results or len(missing_schema) == accessible_count:
        issues.append("no sampled pages expose parseable JSON-LD")
    elif missing_schema:
        issues.append(f"{len(missing_schema)} sampled page(s) are missing parseable JSON-LD")
    if invalid_schema_pages:
        issues.append(f"{len(invalid_schema_pages)} sampled page(s) have invalid JSON-LD")
    if thin_pages:
        issues.append(f"{len(thin_pages)} sampled page(s) have very little extractable text")
    if oversized_pages:
        issues.append(f"{len(oversized_pages)} sampled page(s) exceed common crawler HTML size limits")

    positives: list[str] = []
    if accessible_count:
        positives.append(f"{accessible_count} sampled sitemap page(s) were fetched for metadata")
    if accessible_count and not missing_title:
        positives.append("sampled pages include titles")
    if accessible_count and not missing_canonical and not offsite_canonicals:
        positives.append("sampled pages include same-site canonicals")
    if accessible_count and not blocked:
        robots_detail = "explicit robots directives allow indexing" if explicit_robots_pages else "no blocking robots directives"
        positives.append("sampled pages have " + robots_detail)
    if accessible_count and len(missing_schema) < accessible_count and not invalid_schema_pages:
        positives.append("sampled pages include parseable JSON-LD")
    if accessible_count and not thin_pages and not oversized_pages:
        positives.append("sampled pages have usable extractable content size")

    markdown_positives, markdown_caveats = (
        await _sample_markdown_caveats(page_results, markdown_by_page)
        if page_results
        else ([], [])
    )
    positives.extend(markdown_positives)

    return SamplePageMetadataResult(
        ok=not issues,
        positives=positives,
        issues=issues,
        caveats=markdown_caveats,
    )


async def _validate_hreflang(
    context: AuditContext,
    entries: list[SitemapEntry],
    sampled_responses: list[FetchResult] | None = None,
) -> HreflangValidationResult:
    page_responses: list[FetchResult] = []
    seen_urls: set[str] = set()
    if context.homepage.ok:
        page_responses.append(context.homepage)
        seen_urls.add(_normalise_url_for_comparison(context.homepage.final_url))

    if sampled_responses is None:
        sample_urls: list[str] = []
        for entry in entries[:_HREFLANG_SAMPLE_LIMIT]:
            key = _normalise_url_for_comparison(entry.loc)
            if key in seen_urls:
                continue
            sample_urls.append(entry.loc)
            seen_urls.add(key)
        if sample_urls:
            page_responses.extend(await _fetch_all(sample_urls))
    else:
        for response in sampled_responses[:_HREFLANG_SAMPLE_LIMIT]:
            key = _normalise_url_for_comparison(response.final_url)
            if key in seen_urls:
                continue
            seen_urls.add(key)
            page_responses.append(response)

    page_links: dict[str, tuple[str, list[HreflangLink]]] = {}
    for response in page_responses:
        if not response.ok:
            continue
        links = _hreflang_links(response, BeautifulSoup(response.text, "lxml"))
        if links:
            page_links[_normalise_url_for_comparison(response.final_url)] = (response.final_url, links)

    if not page_links:
        return HreflangValidationResult(False, [], [], [])

    invalid_values = 0
    relative_urls = 0
    missing_hrefs = 0
    duplicate_language_pages = 0
    missing_self_pages = 0
    missing_default_pages = 0
    reciprocal_pairs: list[tuple[str, str]] = []
    reciprocal_seen: set[tuple[str, str]] = set()

    for page_url, links in page_links.values():
        page_key = _normalise_url_for_comparison(page_url)
        languages = [link.language for link in links if link.language]
        if len(languages) != len(set(languages)):
            duplicate_language_pages += 1
        invalid_values += len([link for link in links if not _valid_hreflang_value(link.language)])
        missing_hrefs += len([link for link in links if not link.href])
        relative_urls += len([link for link in links if link.href and not link.is_absolute])

        linked_urls = {
            _normalise_url_for_comparison(link.absolute_url)
            for link in links
            if link.absolute_url
        }
        if page_key not in linked_urls:
            missing_self_pages += 1

        non_default_languages = {link.language for link in links if link.language != "x-default"}
        if len(non_default_languages) > 1 and "x-default" not in languages:
            missing_default_pages += 1

        for link in links:
            alternate_key = _normalise_url_for_comparison(link.absolute_url)
            pair_key = (page_key, alternate_key)
            if (
                link.absolute_url
                and link.language != "x-default"
                and alternate_key != page_key
                and _same_site(link.absolute_url, context.url)
                and pair_key not in reciprocal_seen
                and len(reciprocal_pairs) < _HREFLANG_RECIPROCAL_LIMIT
            ):
                reciprocal_seen.add(pair_key)
                reciprocal_pairs.append((page_url, link.absolute_url))

    reciprocal_missing = 0
    reciprocal_unchecked = 0
    reciprocal_checked = 0
    unknown_pairs = [
        (source_url, alternate_url)
        for source_url, alternate_url in reciprocal_pairs
        if _normalise_url_for_comparison(alternate_url) not in page_links
    ]
    fetched_alternates = await _fetch_all([alternate_url for _, alternate_url in unknown_pairs]) if unknown_pairs else []
    fetched_by_requested = {
        response.requested_url: response
        for response in fetched_alternates
    }

    for source_url, alternate_url in reciprocal_pairs:
        alternate_key = _normalise_url_for_comparison(alternate_url)
        if alternate_key in page_links:
            alternate_links = page_links[alternate_key][1]
        else:
            response = fetched_by_requested.get(alternate_url)
            if not response or not response.ok:
                reciprocal_unchecked += 1
                continue
            alternate_links = _hreflang_links(response, BeautifulSoup(response.text, "lxml"))

        reciprocal_checked += 1
        source_key = _normalise_url_for_comparison(source_url)
        alternate_targets = {
            _normalise_url_for_comparison(link.absolute_url)
            for link in alternate_links
            if link.absolute_url
        }
        if source_key not in alternate_targets:
            reciprocal_missing += 1

    issues: list[str] = []
    if invalid_values:
        issues.append(f"{invalid_values} hreflang value(s) are not valid language-region tags or x-default")
    if missing_hrefs:
        issues.append(f"{missing_hrefs} hreflang link(s) are missing href URLs")
    if relative_urls:
        issues.append(f"{relative_urls} hreflang URL(s) are not absolute HTTP(S) URLs")
    if duplicate_language_pages:
        issues.append(f"{duplicate_language_pages} sampled page(s) publish duplicate hreflang language values")
    if missing_self_pages:
        issues.append(f"{missing_self_pages} sampled page(s) publish hreflang without a self-reference")
    if reciprocal_missing:
        issues.append(f"{reciprocal_missing} sampled hreflang alternate(s) are not reciprocal")

    caveats: list[str] = []
    if missing_default_pages:
        caveats.append(f"{missing_default_pages} multilingual hreflang set(s) do not include x-default")
    if reciprocal_unchecked:
        caveats.append(f"{reciprocal_unchecked} hreflang alternate URL(s) could not be fetched for reciprocal validation")

    positives = [f"hreflang present on {len(page_links)} sampled page(s)"]
    if not issues:
        positives.append("sampled hreflang uses valid language tags, self-references, and absolute URLs")
    if reciprocal_checked and not reciprocal_missing:
        positives.append("sampled same-site hreflang alternates are reciprocal where checked")

    return HreflangValidationResult(True, positives, issues, caveats)


async def _fetch_all(urls: list[str]) -> list[FetchResult]:
    return list(await asyncio.gather(*[fetch_url(url) for url in urls], return_exceptions=False))


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _feed_candidate_urls(soup: BeautifulSoup, base_url: str, root_url: str) -> list[str]:
    urls: list[str] = []
    for tag in soup.find_all("link", rel=lambda value: value and "alternate" in value):
        href = tag.get("href", "").strip()
        if not href:
            continue
        media_type = tag.get("type", "").split(";", 1)[0].strip().lower()
        lowered_href = href.lower()
        if media_type not in _FEED_LINK_TYPES and not lowered_href.endswith((".rss", ".xml", ".atom", ".json")):
            continue
        absolute_url = urljoin(base_url, href)
        if _is_absolute_http_url(absolute_url) and _same_site(absolute_url, root_url):
            urls.append(absolute_url)
    return urls


def _dedupe_urls(urls: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        key = url.rstrip("/")
        if key in seen:
            continue
        deduped.append(url)
        seen.add(key)
    return deduped


def _feed_date_from_text(value: str, today: date) -> tuple[date | None, bool]:
    cleaned = value.strip()
    if not cleaned:
        return None, False

    parsed: date | None = None
    marker = cleaned[:10]
    if re.match(r"^\d{4}-\d{2}-\d{2}$", marker):
        try:
            parsed = date.fromisoformat(marker)
        except ValueError:
            parsed = None

    if parsed is None:
        try:
            parsed_datetime = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
            if parsed_datetime.tzinfo:
                parsed_datetime = parsed_datetime.astimezone(UTC)
            parsed = parsed_datetime.date()
        except ValueError:
            parsed = None

    if parsed is None:
        try:
            parsed_datetime = parsedate_to_datetime(cleaned)
            if parsed_datetime.tzinfo:
                parsed_datetime = parsed_datetime.astimezone(UTC)
            parsed = parsed_datetime.date()
        except (TypeError, ValueError, IndexError, OverflowError):
            parsed = None

    if parsed is None or parsed > today:
        return None, True
    return parsed, False


def _summarise_feed_dates(
    url: str,
    feed_type: str,
    item_date_values: list[list[str]],
    today: date,
) -> FeedParseResult:
    latest_date: date | None = None
    dated_item_count = 0
    invalid_date_count = 0

    for values in item_date_values:
        item_dates: list[date] = []
        for value in values:
            parsed_date, invalid = _feed_date_from_text(value, today)
            if invalid:
                invalid_date_count += 1
            elif parsed_date:
                item_dates.append(parsed_date)
        if item_dates:
            dated_item_count += 1
            item_latest = max(item_dates)
            latest_date = item_latest if latest_date is None else max(latest_date, item_latest)

    return FeedParseResult(
        url=url,
        feed_type=feed_type,
        item_count=len(item_date_values),
        dated_item_count=dated_item_count,
        latest_date=latest_date,
        invalid_date_count=invalid_date_count,
    )


def _xml_item_date_values(item: ET.Element) -> list[str]:
    values: list[str] = []
    for child in item:
        if _xml_local_name(child.tag) in _FEED_DATE_FIELDS and child.text:
            values.append(child.text.strip())
    return values


def _parse_xml_feed(response: FetchResult, today: date) -> FeedParseResult:
    feed_url = response.requested_url or response.final_url
    try:
        root = ET.fromstring(response.text)
    except ET.ParseError:
        return FeedParseResult(feed_url, "feed", 0, 0, None, 0, "feed XML could not be parsed")

    root_name = _xml_local_name(root.tag)
    if root_name == "feed":
        entries = [child for child in root if _xml_local_name(child.tag) == "entry"]
        return _summarise_feed_dates(
            feed_url,
            "Atom",
            [_xml_item_date_values(entry) for entry in entries],
            today,
        )

    if root_name == "rss":
        channel = next((child for child in root if _xml_local_name(child.tag) == "channel"), root)
        items = [child for child in channel if _xml_local_name(child.tag) == "item"]
        return _summarise_feed_dates(
            feed_url,
            "RSS",
            [_xml_item_date_values(item) for item in items],
            today,
        )

    if root_name == "rdf":
        items = [child for child in root if _xml_local_name(child.tag) == "item"]
        return _summarise_feed_dates(
            feed_url,
            "RSS",
            [_xml_item_date_values(item) for item in items],
            today,
        )

    return FeedParseResult(feed_url, "feed", 0, 0, None, 0, "feed root is not RSS, Atom, or JSON Feed")


def _parse_json_feed(response: FetchResult, today: date) -> FeedParseResult:
    feed_url = response.requested_url or response.final_url
    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError:
        return FeedParseResult(feed_url, "JSON Feed", 0, 0, None, 0, "feed JSON could not be parsed")

    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return FeedParseResult(feed_url, "JSON Feed", 0, 0, None, 0, "JSON Feed items were missing")

    item_date_values: list[list[str]] = []
    for item in payload["items"]:
        values: list[str] = []
        if isinstance(item, dict):
            for key in ("date_modified", "date_published", "updated", "published"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    values.append(value)
        item_date_values.append(values)

    return _summarise_feed_dates(feed_url, "JSON Feed", item_date_values, today)


def _parse_feed_response(response: FetchResult, today: date | None = None) -> FeedParseResult:
    today = today or date.today()
    if not response.ok:
        feed_url = response.requested_url or response.final_url
        return FeedParseResult(feed_url, "feed", 0, 0, None, 0, "feed URL was not reachable")

    content_type = response.headers.get("content-type", "").lower()
    stripped = response.text.lstrip()
    if "json" in content_type or stripped.startswith("{"):
        return _parse_json_feed(response, today)
    return _parse_xml_feed(response, today)


def _feed_positive(result: FeedParseResult) -> str:
    noun = result.feed_type if result.feed_type == "JSON Feed" else f"{result.feed_type} feed"
    detail = f"{noun} has {result.item_count} item(s)"
    if result.latest_date:
        detail += f"; latest item dated {result.latest_date.isoformat()}"
    return detail


def _has_fresh_section_link(soup: BeautifulSoup) -> bool:
    for tag in soup.find_all("a", href=True):
        value = (tag.get("href", "") + " " + tag.get_text(" ", strip=True)).lower()
        if any(term in value for term in _FRESH_SECTION_TERMS):
            return True
    return False


def _date_from_text(value: str) -> date | None:
    marker = value.strip()[:10]
    try:
        return date.fromisoformat(marker)
    except ValueError:
        return None


def _has_dated_content(soup: BeautifulSoup) -> bool:
    for tag in soup.find_all("time"):
        if _date_from_text(tag.get("datetime", "") or tag.get_text(strip=True)):
            return True
    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or "").lower()
        if key in {"article:published_time", "article:modified_time", "date", "last-modified"}:
            if _date_from_text(tag.get("content", "")):
                return True
    return False


async def _freshness_surface(context: AuditContext) -> FeedFreshnessResult:
    if not context.homepage.ok:
        return FeedFreshnessResult(
            False,
            [],
            ["homepage was unavailable for freshness-surface detection"],
            [],
        )

    soup = BeautifulSoup(context.homepage.text, "lxml")
    today = date.today()
    positives: list[str] = []
    caveats: list[str] = []

    if _has_fresh_section_link(soup):
        positives.append("blog, news, resource, or update section linked from the homepage")
    if _has_dated_content(soup):
        positives.append("dated content metadata on the homepage")

    linked_feed_urls = _feed_candidate_urls(soup, context.homepage.final_url, context.url)
    common_feed_urls = [make_root_url(context.url, path) for path in _COMMON_FEED_PATHS]
    feed_urls = _dedupe_urls([*linked_feed_urls, *common_feed_urls])[:_FEED_SAMPLE_LIMIT]
    linked_keys = {url.rstrip("/") for url in linked_feed_urls}
    feed_responses = list(
        await asyncio.gather(
            *[fetch_url(url, accept=_FEED_ACCEPT) for url in feed_urls],
            return_exceptions=False,
        )
    ) if feed_urls else []
    feed_results = [_parse_feed_response(response, today=today) for response in feed_responses]
    usable_feeds = [result for result in feed_results if result.parse_error is None and result.item_count > 0]

    if usable_feeds:
        best_feed = max(usable_feeds, key=lambda result: (result.latest_date or date.min, result.item_count))
        source = "linked" if best_feed.url.rstrip("/") in linked_keys else "common endpoint"
        positives.insert(0, f"{source} {_feed_positive(best_feed)}")
        if len(usable_feeds) > 1:
            positives.append(f"{len(usable_feeds)} RSS/Atom/JSON feed candidate(s) were parsed")

    for result in usable_feeds:
        undated_items = result.item_count - result.dated_item_count
        if result.invalid_date_count:
            caveats.append(f"{result.invalid_date_count} feed item date(s) were invalid")
        if result.item_count and result.dated_item_count == 0:
            caveats.append(f"{result.feed_type} feed has {result.item_count} item(s) but no item dates were parsed")
        elif undated_items:
            caveats.append(f"{undated_items} feed item(s) did not include parseable dates")
        if result.latest_date and (today - result.latest_date).days > _FEED_STALE_AFTER_DAYS:
            caveats.append(
                f"latest feed item is older than {_FEED_STALE_AFTER_DAYS} days ({result.latest_date.isoformat()})"
            )

    linked_parse_failures = [
        result
        for result in feed_results
        if result.url.rstrip("/") in linked_keys and result.parse_error is not None
    ]
    if linked_parse_failures:
        caveats.append(f"{len(linked_parse_failures)} linked feed URL(s) were not reachable or parseable")

    empty_feeds = [
        result
        for result in feed_results
        if result.parse_error is None and result.item_count == 0
    ]
    if empty_feeds:
        caveats.append(f"{len(empty_feeds)} feed candidate(s) parsed but contained no items")

    if positives:
        if not usable_feeds:
            caveats.append("freshness is inferred from homepage update signals; no parseable feed item dates were available")
        return FeedFreshnessResult(True, positives, [], caveats)

    return FeedFreshnessResult(
        False,
        [],
        ["no usable RSS/Atom/JSON feed, dated content, or publishing section was found in the sampled surfaces"],
        caveats,
    )


async def check_search_discovery(
    context: AuditContext,
    include_ecommerce: bool = False,
    qa2_evidence: "QA2EvidenceBundle | None" = None,
) -> CheckResult:
    """Check included cross-engine discovery hints for Google, Bing, and Brave."""
    score = 0
    issues: list[str] = []
    positives: list[str] = []

    bots_allowed, blocked = _search_bots_allowed(context.robots.text if context.robots.ok else "", context.url + "/")
    if bots_allowed:
        score += 1
        positives.append("Googlebot and Bingbot are not blocked by robots.txt")
    else:
        issues.append("robots.txt blocks: " + ", ".join(blocked))

    sitemap_sample = (
        qa2_evidence.sitemap_sample
        if qa2_evidence is not None
        else await collect_sitemap_sample(context)
    )
    robots_sitemaps = sitemap_urls_from_robots(context.robots.text) if context.robots.ok else []

    if sitemap_sample.is_valid:
        score += 1
        sitemap_detail = f"{len(sitemap_sample.entries)} sampled sitemap URL(s)"
        if sitemap_sample.is_index:
            sitemap_detail += f" across {sitemap_sample.sitemap_count} sitemap file(s)"
        positives.append("valid sitemap discovery with " + sitemap_detail)
    else:
        issues.append("no valid sitemap was found from /sitemap.xml or robots.txt references")

    if not robots_sitemaps:
        issues.append("robots.txt does not reference a sitemap")

    freshness_result = await _freshness_surface(context)
    lastmod_count = len([entry for entry in sitemap_sample.entries if entry.lastmod])
    lastmod_ratio = lastmod_count / len(sitemap_sample.entries) if sitemap_sample.entries else 0
    freshness_ok = (
        sitemap_sample.is_valid
        and lastmod_ratio >= 0.8
        and sitemap_sample.invalid_lastmod_count == 0
        and sitemap_sample.future_lastmod_count == 0
    ) or freshness_result.available

    if freshness_ok:
        score += 1
        if lastmod_ratio >= 0.8:
            positives.append("sitemap has usable lastmod freshness signals")
        positives.extend(freshness_result.positives[:2])
    else:
        issues.append("sitemap lacks reliable lastmod dates and no feed or publishing freshness surface was found")
        issues.extend(freshness_result.issues[:2])

    sitemap_hygiene_ok = (
        sitemap_sample.is_valid
        and sitemap_sample.same_host_count == len(sitemap_sample.entries)
        and sitemap_sample.non_https_count == 0
        and sitemap_sample.duplicate_count == 0
    )
    if qa2_evidence is None:
        sample_metadata = await _sample_pages_have_discovery_metadata(
            context, sitemap_sample.entries
        )
        hreflang_result = await _validate_hreflang(context, sitemap_sample.entries)
        blurb_issues: list[str] = []
    else:
        sample_metadata = await _sample_pages_have_discovery_metadata(
            context,
            sitemap_sample.entries,
            list(qa2_evidence.sample_page_responses),
            dict(qa2_evidence.sample_markdown_by_page),
        )
        hreflang_result = await _validate_hreflang(
            context,
            sitemap_sample.entries,
            list(qa2_evidence.sample_page_responses),
        )
        blurb_issues = list(qa2_evidence.search_blurb.issues)
    if sitemap_hygiene_ok and sample_metadata.ok and not blurb_issues:
        score += 1
        positives.append(
            "sampled sitemap URLs are same-site, HTTPS, accessible, indexable, and metadata-readable"
        )
        positives.extend(sample_metadata.positives[:3])
        # Fable 5 nit (F4-17 c): when the pass branch is taken, any blurb
        # issues that accumulated above were silently dropped. Surface them
        # as non-scoring caveats so the operator sees the proxy notes rather
        # than losing the signal on a clean-looking row.
        caveats_for_pass = [f"Search blurb note: {issue}" for issue in blurb_issues]
        positives.extend(caveats_for_pass)
    else:
        if sitemap_sample.non_https_count:
            issues.append(f"{sitemap_sample.non_https_count} sampled sitemap URL(s) are not HTTPS")
        if sitemap_sample.same_host_count != len(sitemap_sample.entries):
            issues.append("some sampled sitemap URLs point off-site")
        if sitemap_sample.duplicate_count:
            issues.append(f"{sitemap_sample.duplicate_count} duplicate sitemap URL(s) were found")
        issues.extend(sample_metadata.issues)
        issues.extend(f"search-blurb proxy: {issue}" for issue in blurb_issues[:3])

    if hreflang_result.present:
        positives.extend(hreflang_result.positives[:2])
        if hreflang_result.issues:
            issues.extend(hreflang_result.issues)
            score = min(score, 3)

    trust_result = _trust_surface_discovery(context, sitemap_sample.entries, include_ecommerce)
    positives.extend(trust_result.positives[:2])
    if trust_result.issues:
        issues.extend(trust_result.issues)
        score = min(score, 3)

    metadata_caveats = [
        *freshness_result.caveats[:2],
        *sample_metadata.caveats[:2],
        *hreflang_result.caveats[:2],
        *trust_result.caveats[:2],
    ]
    if qa2_evidence is not None:
        metadata_caveats.append(
            "page-owned blurb coherence is inferred; actual DuckDuckGo and Bing snippet display was not verified"
        )

    if score == 4:
        state = "pass"
        finding = "Included search discovery hints are strong: " + "; ".join(positives) + "."
        fix = (
            "No action needed for the included discovery-hint check. Actual index coverage, "
            "rankings, and branded demand require external search data."
        )
        if metadata_caveats:
            finding += " Discovery caveat: " + "; ".join(metadata_caveats) + "."
            fix = (
                "Optional: keep RSS/Atom/JSON feeds populated with parseable recent item dates, "
                "expose page-level Markdown alternates or index.md exports for important "
                "URLs, and keep trust/support pages linked when relevant. Actual index coverage, "
                "rankings, and branded demand require external search data."
            )
    elif score:
        state = "partial"
        visible_issues = [*issues, *metadata_caveats]
        finding = (
            "Some included search discovery hints are missing: "
            + "; ".join(visible_issues)
            + ". This does not prove the site is absent from Google, Bing, or Brave indexes."
        )
        fix = (
            "Expose a valid sitemap, reference it in robots.txt, keep sitemap URLs clean "
            "and indexable, publish reliable lastmod dates or an RSS/Atom/JSON feed/update "
            "surface with parseable item dates, "
            "ensure Googlebot and Bingbot are not blocked, and keep sampled pages readable "
            "with titles, canonicals, robots directives, JSON-LD, useful text or Markdown alternates, "
            "valid hreflang alternates when multilingual pages publish them, and same-site "
            "About, Contact, Privacy, Terms, Support, Returns, or Shipping surfaces when relevant."
        )
        if blurb_issues:
            fix += (
                " Align page-owned titles and meta descriptions with each page's H1, main "
                "content, canonical URL, and optional social descriptions; keep sampled "
                "titles and descriptions distinct and current."
            )
    else:
        state = "warn"
        finding = (
            "No strong included search discovery hints were found. This is not the same as "
            "checking real Google, Bing, or Brave index coverage."
        )
        fix = (
            "Publish a valid sitemap with reliable lastmod dates, reference it in robots.txt, "
            "keep listed URLs accessible and indexable, expose a feed or update surface with parseable "
            "item dates when relevant, "
            "allow Googlebot and Bingbot, keep hreflang valid when multilingual pages publish it, "
            "and link core trust/entity pages from navigation or the sitemap."
        )

    return CheckResult(
        pillar="seo",
        check_name="search_discovery",
        label="Search Discovery Hints",
        state=state,
        evidence_level="inferred",
        score=score,
        max_score=4,
        finding=finding,
        fix=fix,
        effort="low",
    )
