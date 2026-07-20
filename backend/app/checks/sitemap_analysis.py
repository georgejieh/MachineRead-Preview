import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urljoin, urlparse

from app.audit_context import AuditContext
from app.fetching import FetchResult, fetch_url

_SITEMAP_NAMESPACE = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class SitemapEntry:
    loc: str
    lastmod: str | None


@dataclass(frozen=True)
class ParsedSitemap:
    is_valid: bool
    loc_count: int
    has_lastmod: bool
    is_index: bool
    entries: list[SitemapEntry]
    sitemap_locs: list[str]
    invalid_lastmod_count: int
    future_lastmod_count: int
    missing_lastmod_count: int


@dataclass(frozen=True)
class SitemapSampleResult:
    entries: list[SitemapEntry]
    sitemap_count: int
    is_index: bool
    has_robot_reference: bool
    invalid_lastmod_count: int
    future_lastmod_count: int
    missing_lastmod_count: int
    same_host_count: int
    non_https_count: int
    duplicate_count: int
    issues: list[str]

    @property
    def is_valid(self) -> bool:
        return bool(self.entries)

    @property
    def has_lastmod(self) -> bool:
        return any(entry.lastmod for entry in self.entries)


@dataclass(frozen=True)
class SitemapCollection:
    sample: SitemapSampleResult
    responses: tuple[FetchResult, ...]
    scoring_responses: tuple[FetchResult, ...]


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def sitemap_urls_from_robots(content: str) -> list[str]:
    urls: list[str] = []
    for line in content.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip().lower() == "sitemap":
            urls.append(value.strip())
    return urls


def _child_text(element: ET.Element, name: str) -> str | None:
    child = element.find(f"{_SITEMAP_NAMESPACE}{name}")
    if child is None:
        child = element.find(name)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _lastmod_date(value: str | None) -> date | None:
    if not value:
        return None
    cleaned = value.strip()
    if not _DATE_PREFIX.match(cleaned):
        return None
    try:
        if cleaned.endswith("Z"):
            return datetime.fromisoformat(cleaned.replace("Z", "+00:00")).date()
        if "T" in cleaned:
            parsed = datetime.fromisoformat(cleaned)
            if parsed.tzinfo:
                parsed = parsed.astimezone(UTC)
            return parsed.date()
        return date.fromisoformat(cleaned[:10])
    except ValueError:
        return None


def parse_sitemap(content: str, today: date | None = None) -> ParsedSitemap:
    today = today or date.today()
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return ParsedSitemap(False, 0, False, False, [], [], 0, 0, 0)

    root_name = local_name(root.tag)
    if root_name not in {"urlset", "sitemapindex"}:
        return ParsedSitemap(False, 0, False, False, [], [], 0, 0, 0)

    entries: list[SitemapEntry] = []
    sitemap_locs: list[str] = []
    invalid_lastmod_count = 0
    future_lastmod_count = 0
    missing_lastmod_count = 0

    if root_name == "urlset":
        for element in root:
            if local_name(element.tag) != "url":
                continue
            loc = _child_text(element, "loc")
            if not loc:
                continue
            lastmod = _child_text(element, "lastmod")
            parsed_lastmod = _lastmod_date(lastmod)
            if not lastmod:
                missing_lastmod_count += 1
            elif parsed_lastmod is None:
                invalid_lastmod_count += 1
            elif parsed_lastmod > today:
                future_lastmod_count += 1
            entries.append(SitemapEntry(loc=loc, lastmod=lastmod))
    else:
        for element in root:
            if local_name(element.tag) != "sitemap":
                continue
            loc = _child_text(element, "loc")
            if loc:
                sitemap_locs.append(loc)

    loc_count = len(entries) + len(sitemap_locs)
    return ParsedSitemap(
        is_valid=loc_count > 0,
        loc_count=loc_count,
        has_lastmod=any(entry.lastmod for entry in entries),
        is_index=root_name == "sitemapindex",
        entries=entries,
        sitemap_locs=sitemap_locs,
        invalid_lastmod_count=invalid_lastmod_count,
        future_lastmod_count=future_lastmod_count,
        missing_lastmod_count=missing_lastmod_count,
    )


def analyse_sitemap_basic(content: str) -> tuple[bool, int, bool, bool]:
    parsed = parse_sitemap(content)
    return parsed.is_valid, parsed.loc_count, parsed.has_lastmod, parsed.is_index


def _same_site(url: str, root_hostname: str | None) -> bool:
    hostname = urlparse(url).hostname
    if not hostname or not root_hostname:
        return False
    return hostname.removeprefix("www.") == root_hostname.removeprefix("www.")


def _candidate_key(response: FetchResult) -> str:
    return response.final_url or response.requested_url


async def collect_sitemap_evidence(
    context: AuditContext,
    sitemap_limit: int = 4,
    url_limit: int = 25,
) -> SitemapCollection:
    robots_sitemaps = sitemap_urls_from_robots(context.robots.text) if context.robots.ok else []
    candidates = [context.sitemap] if context.sitemap.ok else []
    responses: list[FetchResult] = [context.sitemap]
    seen = {
        key
        for candidate in responses
        for key in (candidate.requested_url, _candidate_key(candidate))
    }
    issues: list[str] = []

    for sitemap_url in robots_sitemaps[:sitemap_limit]:
        absolute_url = urljoin(context.url, sitemap_url)
        if absolute_url in seen:
            continue
        response = await fetch_url(absolute_url)
        responses.append(response)
        key = _candidate_key(response)
        if response.ok and key not in seen:
            candidates.append(response)
        seen.update((absolute_url, response.requested_url, key))

    entries: list[SitemapEntry] = []
    sitemap_count = 0
    is_index = False
    invalid_lastmod_count = 0
    future_lastmod_count = 0
    missing_lastmod_count = 0

    for candidate in candidates[:sitemap_limit]:
        parsed = parse_sitemap(candidate.text)
        if not parsed.is_valid:
            issues.append(f"{candidate.final_url} is not a valid sitemap")
            continue

        sitemap_count += 1
        is_index = is_index or parsed.is_index
        invalid_lastmod_count += parsed.invalid_lastmod_count
        future_lastmod_count += parsed.future_lastmod_count
        missing_lastmod_count += parsed.missing_lastmod_count
        entries.extend(parsed.entries[: max(url_limit - len(entries), 0)])

        if parsed.is_index and len(entries) < url_limit:
            child_urls = parsed.sitemap_locs[: max(sitemap_limit - sitemap_count, 0)]
            child_responses: list[FetchResult] = []
            for child_url in child_urls:
                absolute_url = urljoin(candidate.final_url, child_url)
                if absolute_url in seen:
                    continue
                child_response = await fetch_url(absolute_url)
                responses.append(child_response)
                child_responses.append(child_response)
                seen.update(
                    (
                        absolute_url,
                        child_response.requested_url,
                        _candidate_key(child_response),
                    )
                )
            for child_response in child_responses:
                child_parsed = parse_sitemap(child_response.text) if child_response.ok else None
                if child_parsed is None or not child_parsed.is_valid:
                    issues.append(f"{child_response.final_url} could not be parsed as a sitemap")
                    continue
                sitemap_count += 1
                invalid_lastmod_count += child_parsed.invalid_lastmod_count
                future_lastmod_count += child_parsed.future_lastmod_count
                missing_lastmod_count += child_parsed.missing_lastmod_count
                entries.extend(child_parsed.entries[: max(url_limit - len(entries), 0)])
                if len(entries) >= url_limit:
                    break

        if len(entries) >= url_limit:
            break

    root_hostname = urlparse(context.homepage.final_url if context.homepage.ok else context.url).hostname
    unique_locs = {entry.loc for entry in entries}
    same_host_count = sum(1 for entry in entries if _same_site(entry.loc, root_hostname))
    non_https_count = sum(1 for entry in entries if urlparse(entry.loc).scheme != "https")
    duplicate_count = len(entries) - len(unique_locs)

    return SitemapCollection(
        sample=SitemapSampleResult(
            entries=entries,
            sitemap_count=sitemap_count,
            is_index=is_index,
            has_robot_reference=bool(robots_sitemaps),
            invalid_lastmod_count=invalid_lastmod_count,
            future_lastmod_count=future_lastmod_count,
            missing_lastmod_count=missing_lastmod_count,
            same_host_count=same_host_count,
            non_https_count=non_https_count,
            duplicate_count=duplicate_count,
            issues=issues,
        ),
        responses=tuple(responses),
        scoring_responses=tuple(candidates),
    )


async def collect_sitemap_sample(
    context: AuditContext,
    sitemap_limit: int = 4,
    url_limit: int = 25,
) -> SitemapSampleResult:
    collection = await collect_sitemap_evidence(context, sitemap_limit, url_limit)
    return collection.sample
