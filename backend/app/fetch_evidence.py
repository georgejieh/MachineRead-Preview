import asyncio
from dataclasses import dataclass
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.audit_context import AuditContext
from app.checks.extraction_readiness import (
    ExtractionReadinessAnalysis,
    ExtractionReadinessInput,
    analyse_extraction_readiness,
)
from app.checks.llms_txt import MarkdownAccessResult, analyse_markdown_response
from app.checks.search_blurb import BlurbPageInput, SearchBlurbAnalysis, analyse_search_blurbs
from app.checks.search_discovery import _index_md_url, _markdown_alternate_url
from app.checks.sitemap_analysis import (
    SitemapSampleResult,
    collect_sitemap_evidence,
    parse_sitemap,
)
from app.fetching import FetchResult, fetch_url, make_root_url

_MARKDOWN_ACCEPT = "text/markdown, text/plain;q=0.9, text/html;q=0.4,*/*;q=0.1"
_MAX_HTML_BYTES = 15 * 1024 * 1024
_MAX_TEXT_BYTES = 1024 * 1024
_SAMPLE_PAGE_LIMIT = 5


@dataclass(frozen=True)
class FetchEvidence:
    sitemap_sample: SitemapSampleResult
    sitemap_score_evidence: tuple[bool, int, bool, bool, bool]
    sitemap_responses: tuple[FetchResult, ...]
    sample_page_responses: tuple[FetchResult, ...]
    sample_markdown_by_page: tuple[tuple[str, FetchResult], ...]
    llms_response: FetchResult
    homepage_markdown_result: MarkdownAccessResult
    homepage_markdown_responses: tuple[FetchResult, ...]
    search_blurb: SearchBlurbAnalysis
    extraction_readiness: ExtractionReadinessAnalysis


def _cap_text(value: str, byte_limit: int) -> str:
    encoded = value.encode("utf-8", errors="ignore")
    if len(encoded) <= byte_limit:
        return value
    return encoded[:byte_limit].decode("utf-8", errors="ignore")


def _capped_response(response: FetchResult, byte_limit: int) -> FetchResult:
    return FetchResult(
        requested_url=response.requested_url,
        final_url=response.final_url,
        status_code=response.status_code,
        headers=dict(response.headers),
        text=_cap_text(response.text, byte_limit),
        elapsed_ms=response.elapsed_ms,
        redirect_chain=list(response.redirect_chain),
        error=response.error,
    )


def _url_key(value: str) -> str:
    parsed = urlparse(value)
    path = parsed.path.rstrip("/") or "/"
    return parsed._replace(
        scheme=parsed.scheme.casefold(),
        netloc=parsed.netloc.casefold(),
        path=path,
        fragment="",
    ).geturl()


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


async def _collect_homepage_markdown(
    context: AuditContext,
) -> tuple[MarkdownAccessResult, tuple[FetchResult, ...]]:
    responses: list[FetchResult] = []
    issues: list[str] = []
    negotiated = _capped_response(
        await fetch_url(context.url, accept=_MARKDOWN_ACCEPT),
        _MAX_TEXT_BYTES,
    )
    responses.append(negotiated)
    result = analyse_markdown_response(
        negotiated,
        "homepage Markdown negotiation",
        require_vary_accept=True,
    )
    if result.available:
        return result, tuple(responses)
    issues.extend(result.issues[:2])

    for path in ("/index.md", "/README.md"):
        response = _capped_response(
            await fetch_url(make_root_url(context.url, path), accept=_MARKDOWN_ACCEPT),
            _MAX_TEXT_BYTES,
        )
        responses.append(response)
        result = analyse_markdown_response(response, f"{path} fallback")
        if result.available:
            return result, tuple(responses)
        issues.extend(result.issues[:1])

    fallback_issue = (
        "no negotiated Markdown/plain response or direct Markdown fallback returned "
        "meaningful text with a Markdown/plain Content-Type"
    )
    return (
        MarkdownAccessResult(
            False,
            None,
            _dedupe([fallback_issue, *issues])[:4],
            [],
        ),
        tuple(responses),
    )


def _homepage_matches(context: AuditContext, url: str) -> bool:
    homepage_keys = {
        _url_key(context.url),
        _url_key(context.homepage.requested_url),
        _url_key(context.homepage.final_url),
    }
    return _url_key(url) in homepage_keys


async def _collect_sample_pages(
    context: AuditContext,
    sample: SitemapSampleResult,
) -> tuple[FetchResult, ...]:
    urls = [entry.loc for entry in sample.entries[:_SAMPLE_PAGE_LIMIT]]

    responses: list[FetchResult | None] = [None] * len(urls)
    fetch_indexes: dict[str, list[int]] = {}
    fetch_urls: dict[str, str] = {}
    for index, url in enumerate(urls):
        if _homepage_matches(context, url):
            responses[index] = _capped_response(context.homepage, _MAX_HTML_BYTES)
        else:
            key = _url_key(url)
            fetch_indexes.setdefault(key, []).append(index)
            fetch_urls.setdefault(key, url)

    if fetch_urls:
        fetched = await asyncio.gather(*(fetch_url(url) for url in fetch_urls.values()))
        for key, response in zip(fetch_urls, fetched, strict=True):
            capped = _capped_response(response, _MAX_HTML_BYTES)
            for index in fetch_indexes[key]:
                responses[index] = capped
    return tuple(response for response in responses if response is not None)


async def _collect_sample_markdown(
    page_responses: tuple[FetchResult, ...],
    homepage_markdown_responses: tuple[FetchResult, ...],
) -> tuple[tuple[str, FetchResult], ...]:
    root_by_url: dict[str, FetchResult] = {}
    for response in homepage_markdown_responses:
        keys = {
            _url_key(response.requested_url),
            _url_key(response.final_url),
        }
        for key in keys:
            root_by_url[key] = response
    preselected_by_page: dict[str, FetchResult] = {}
    pending: dict[str, str] = {}
    candidates: list[tuple[str, str]] = []
    for response in page_responses:
        if not response.ok:
            continue
        negotiated = root_by_url.get(_url_key(response.final_url))
        if negotiated is not None and analyse_markdown_response(
            negotiated, "sampled page Markdown alternate"
        ).available:
            preselected_by_page[response.final_url] = negotiated
            continue
        soup = BeautifulSoup(response.text, "lxml")
        candidate = _markdown_alternate_url(response, soup) or _index_md_url(response)
        candidates.append((response.final_url, candidate))
        key = _url_key(candidate)
        if key not in root_by_url:
            pending.setdefault(key, candidate)

    fetched_by_url: dict[str, FetchResult] = {}
    if pending:
        fetched = await asyncio.gather(
            *(fetch_url(url, accept=_MARKDOWN_ACCEPT) for url in pending.values())
        )
        fetched_by_url = {
            key: _capped_response(response, _MAX_TEXT_BYTES)
            for key, response in zip(pending, fetched, strict=True)
        }

    by_page: list[tuple[str, FetchResult]] = list(preselected_by_page.items())
    for page_url, candidate in candidates:
        key = _url_key(candidate)
        response = root_by_url.get(key) or fetched_by_url[key]
        by_page.append((page_url, response))
    return tuple(by_page)


def _best_sitemap_xml(responses: tuple[FetchResult, ...]) -> str | None:
    candidates = [response for response in responses if response.ok]
    if not candidates:
        return None
    best = max(candidates, key=lambda response: parse_sitemap(response.text).loc_count)
    return best.text if parse_sitemap(best.text).is_valid else None


def _sitemap_score_evidence(
    responses: tuple[FetchResult, ...],
    has_robot_reference: bool,
) -> tuple[bool, int, bool, bool, bool]:
    best = (False, 0, False, False)
    for response in responses:
        if not response.ok:
            continue
        parsed = parse_sitemap(response.text)
        analysed = (
            parsed.is_valid,
            parsed.loc_count,
            parsed.has_lastmod,
            parsed.is_index,
        )
        if analysed[1] > best[1]:
            best = analysed
    return (*best, has_robot_reference)


async def collect_fetch_evidence(
    context: AuditContext,
    include_ecommerce: bool = False,
) -> FetchEvidence:
    sitemap_collection, llms_response, markdown_evidence = await asyncio.gather(
        collect_sitemap_evidence(context),
        fetch_url(make_root_url(context.url, "/llms.txt")),
        _collect_homepage_markdown(context),
    )
    sitemap_responses = tuple(
        _capped_response(response, _MAX_TEXT_BYTES)
        for response in sitemap_collection.responses
    )
    scoring_sitemap_responses = tuple(
        _capped_response(response, _MAX_TEXT_BYTES)
        for response in sitemap_collection.scoring_responses
    )
    llms_response = _capped_response(llms_response, _MAX_TEXT_BYTES)
    homepage_markdown_result, homepage_markdown_responses = markdown_evidence
    sample_page_responses = await _collect_sample_pages(context, sitemap_collection.sample)
    sample_markdown_by_page = await _collect_sample_markdown(
        sample_page_responses,
        homepage_markdown_responses,
    )

    homepage = _capped_response(context.homepage, _MAX_HTML_BYTES)
    blurb_inputs = [BlurbPageInput(homepage.final_url, homepage.text)]
    seen_pages = {_url_key(homepage.final_url)}
    for response in sample_page_responses:
        key = _url_key(response.final_url)
        if response.ok and key not in seen_pages:
            seen_pages.add(key)
            blurb_inputs.append(BlurbPageInput(response.final_url, response.text))

    extraction = analyse_extraction_readiness(
        ExtractionReadinessInput(
            url=homepage.final_url,
            raw_html=homepage.text,
            markdown_responses=homepage_markdown_responses,
            llms_txt=llms_response.text if llms_response.ok else None,
            sitemap_xml=_best_sitemap_xml(sitemap_responses),
            include_ecommerce=include_ecommerce,
        )
    )
    return FetchEvidence(
        sitemap_sample=sitemap_collection.sample,
        sitemap_score_evidence=_sitemap_score_evidence(
            scoring_sitemap_responses,
            sitemap_collection.sample.has_robot_reference,
        ),
        sitemap_responses=sitemap_responses,
        sample_page_responses=sample_page_responses,
        sample_markdown_by_page=sample_markdown_by_page,
        llms_response=llms_response,
        homepage_markdown_result=homepage_markdown_result,
        homepage_markdown_responses=homepage_markdown_responses,
        search_blurb=analyse_search_blurbs(blurb_inputs),
        extraction_readiness=extraction,
    )
