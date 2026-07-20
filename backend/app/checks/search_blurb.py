import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


_WORD_RE = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)?", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")
_MIN_DESCRIPTION_LENGTH = 50
_MAX_DESCRIPTION_LENGTH = 200
_MIN_OVERLAP_DESCRIPTION_TOKENS = 5
_MIN_OVERLAP_CONTENT_TOKENS = 12
_LOW_OVERLAP_THRESHOLD = 0.20
_SOCIAL_MISMATCH_THRESHOLD = 0.30
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_PLACEHOLDER_PATTERNS = (
    re.compile(r"\blorem ipsum\b", re.IGNORECASE),
    re.compile(r"\b(?:meta )?description (?:goes|comes) here\b", re.IGNORECASE),
    re.compile(r"\b(?:insert|add|write) (?:a |your )?description\b", re.IGNORECASE),
    re.compile(r"\bcoming soon\b", re.IGNORECASE),
    re.compile(r"^welcome to (?:our|my|the) (?:website|site)[.!]?$", re.IGNORECASE),
)
_BOILERPLATE_PATTERNS = (
    re.compile(r"\ball rights reserved\b", re.IGNORECASE),
    re.compile(r"\bwe use cookies\b", re.IGNORECASE),
    re.compile(r"\bprivacy policy\b.*\bterms (?:of (?:use|service)|and conditions)\b", re.IGNORECASE),
)
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
        "is", "it", "of", "on", "or", "our", "that", "the", "this", "to", "we",
        "with", "you", "your",
    }
)


@dataclass(frozen=True)
class BlurbPageInput:
    url: str
    html: str


@dataclass(frozen=True)
class PageBlurbSignals:
    url: str
    title: str | None
    meta_description: str | None
    h1: str | None
    main_text: str
    canonical_url: str | None
    open_graph_description: str | None
    twitter_description: str | None


@dataclass(frozen=True)
class SearchBlurbAnalysis:
    pages: tuple[PageBlurbSignals, ...]
    positives: tuple[str, ...]
    issues: tuple[str, ...]
    caveats: tuple[str, ...]


def _clean_text(value: str) -> str:
    return _SPACE_RE.sub(" ", value).strip()


def _meta_content(soup: BeautifulSoup, *, name: str | None = None, prop: str | None = None) -> str | None:
    attrs = {"name": name} if name else {"property": prop}
    tag = soup.find("meta", attrs=attrs)
    content = _clean_text(str(tag.get("content", ""))) if tag else ""
    return content or None


def _canonical(soup: BeautifulSoup, url: str) -> str | None:
    for tag in soup.find_all("link"):
        rel = tag.get("rel", [])
        rel_values = rel.lower().split() if isinstance(rel, str) else [str(value).lower() for value in rel]
        if "canonical" in rel_values:
            href = _clean_text(str(tag.get("href", "")))
            if not href:
                return None
            try:
                return urljoin(url, href)
            except ValueError:
                return href
    return None


def extract_page_blurb(page: BlurbPageInput) -> PageBlurbSignals:
    """Extract the page-owned signals that can influence a search-result blurb."""
    soup = BeautifulSoup(page.html, "lxml")
    title = _clean_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
    h1_tag = soup.find("h1")
    h1 = _clean_text(h1_tag.get_text(" ", strip=True)) if h1_tag else ""

    content_soup = BeautifulSoup(page.html, "lxml")
    for tag in content_soup(["script", "style", "noscript", "template", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()
    main = content_soup.find("main") or content_soup.find("article") or content_soup.body or content_soup
    main_text = _clean_text(main.get_text(" ", strip=True))

    return PageBlurbSignals(
        url=page.url,
        title=title or None,
        meta_description=_meta_content(soup, name="description"),
        h1=h1 or None,
        main_text=main_text,
        canonical_url=_canonical(soup, page.url),
        open_graph_description=_meta_content(soup, prop="og:description"),
        twitter_description=_meta_content(soup, name="twitter:description"),
    )


def _normalise(value: str) -> str:
    return " ".join(_WORD_RE.findall(value.casefold().replace("-", " ").replace("'", " ")))


def _normalise_url(value: str) -> str | None:
    try:
        parsed = urlparse(value)
        scheme = parsed.scheme.casefold()
        hostname = (parsed.hostname or "").casefold()
        port = parsed.port
    except ValueError:
        return None
    if scheme not in {"http", "https"} or not hostname:
        return None
    netloc = hostname
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    path = parsed.path.rstrip("/") or "/"
    return parsed._replace(scheme=scheme, netloc=netloc, path=path, params="", query="", fragment="").geturl()


def _same_host(left: str, right: str) -> bool:
    try:
        left_host = (urlparse(left).hostname or "").casefold()
        right_host = (urlparse(right).hostname or "").casefold()
    except ValueError:
        return False
    return bool(left_host) and left_host == right_host


def _years(value: str | None) -> set[int]:
    return {int(year) for year in _YEAR_RE.findall(value or "")}


def _tokens(value: str) -> set[str]:
    return {token for token in _WORD_RE.findall(value.casefold()) if token not in _STOPWORDS and len(token) > 1}


def _token_coverage(left: str, right: str) -> float | None:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if len(left_tokens) < _MIN_OVERLAP_DESCRIPTION_TOKENS or len(right_tokens) < _MIN_OVERLAP_CONTENT_TOKENS:
        return None
    return len(left_tokens & right_tokens) / len(left_tokens)


def _looks_placeholder(value: str) -> bool:
    return any(pattern.search(value) for pattern in (*_PLACEHOLDER_PATTERNS, *_BOILERPLATE_PATTERNS))


def _mismatched_social_description(meta_description: str, social_description: str) -> bool:
    if _normalise(meta_description) == _normalise(social_description):
        return False
    meta_tokens = _tokens(meta_description)
    social_tokens = _tokens(social_description)
    if (
        len(meta_tokens) < _MIN_OVERLAP_DESCRIPTION_TOKENS
        or len(social_tokens) < _MIN_OVERLAP_DESCRIPTION_TOKENS
    ):
        return False
    overlap = len(meta_tokens & social_tokens) / len(meta_tokens)
    return overlap < _SOCIAL_MISMATCH_THRESHOLD


def analyse_search_blurbs(page_inputs: tuple[BlurbPageInput, ...] | list[BlurbPageInput]) -> SearchBlurbAnalysis:
    """Assess deterministic page-owned blurb proxies without querying a search engine."""
    pages = tuple(extract_page_blurb(page) for page in page_inputs)
    positives: list[str] = []
    issues: list[str] = []
    caveats: list[str] = [
        "Page-owned metadata was assessed; actual DuckDuckGo or Bing snippet selection and display were not verified."
    ]

    if not pages:
        issues.append("No pages were supplied for search-result blurb analysis.")
        return SearchBlurbAnalysis(pages, tuple(positives), tuple(issues), tuple(caveats))

    missing_title = sum(page.title is None for page in pages)
    missing_description = sum(page.meta_description is None for page in pages)
    missing_h1 = sum(page.h1 is None for page in pages)
    missing_main = sum(not page.main_text for page in pages)
    missing_canonical = sum(page.canonical_url is None for page in pages)
    for count, label in (
        (missing_title, "title"),
        (missing_description, "meta description"),
        (missing_h1, "H1"),
        (missing_main, "extractable main text"),
        (missing_canonical, "canonical URL"),
    ):
        if count:
            issues.append(f"{count} sampled page(s) are missing {label}.")
        else:
            positives.append(f"All {len(pages)} sampled page(s) include {label}.")

    descriptions = [(page.url, page.meta_description) for page in pages if page.meta_description]
    short = sum(len(description) < _MIN_DESCRIPTION_LENGTH for _, description in descriptions)
    long = sum(len(description) > _MAX_DESCRIPTION_LENGTH for _, description in descriptions)
    if short:
        issues.append(f"{short} sampled meta description(s) are shorter than {_MIN_DESCRIPTION_LENGTH} characters.")
    if long:
        issues.append(f"{long} sampled meta description(s) are longer than {_MAX_DESCRIPTION_LENGTH} characters.")
    if descriptions and not short and not long:
        positives.append("Sampled meta descriptions stay within the conservative 50-200 character review range.")

    normalised_urls: dict[str, list[str]] = {}
    for url, description in descriptions:
        normalised_urls.setdefault(_normalise(description), []).append(url)
    duplicate_groups = [urls for value, urls in normalised_urls.items() if value and len(urls) > 1]
    if duplicate_groups:
        duplicate_page_count = sum(len(urls) for urls in duplicate_groups)
        issues.append(
            f"{duplicate_page_count} sampled page(s) reuse a normalized meta description across "
            f"{len(duplicate_groups)} duplicate group(s)."
        )
    elif len(descriptions) > 1:
        positives.append("Sampled meta descriptions are distinct after normalization.")

    titles = [(page.url, page.title) for page in pages if page.title]
    normalised_title_urls: dict[str, list[str]] = {}
    for url, title in titles:
        normalised_title_urls.setdefault(_normalise(title), []).append(url)
    duplicate_title_groups = [
        urls for value, urls in normalised_title_urls.items() if value and len(urls) > 1
    ]
    if duplicate_title_groups:
        duplicate_title_count = sum(len(urls) for urls in duplicate_title_groups)
        issues.append(
            f"{duplicate_title_count} sampled page(s) reuse a normalized title across "
            f"{len(duplicate_title_groups)} duplicate group(s)."
        )
    elif len(titles) > 1:
        positives.append("Sampled titles are distinct after normalization.")

    canonical_pages = [(page.url, page.canonical_url) for page in pages if page.canonical_url]
    malformed_canonicals = sum(_normalise_url(canonical) is None for _, canonical in canonical_pages)
    off_host_canonicals = sum(
        _normalise_url(canonical) is not None and not _same_host(url, canonical)
        for url, canonical in canonical_pages
    )
    non_self_canonicals = sum(
        _normalise_url(canonical) is not None
        and _same_host(url, canonical)
        and _normalise_url(url) != _normalise_url(canonical)
        for url, canonical in canonical_pages
    )
    canonical_targets: dict[str, set[str]] = {}
    for url, canonical in canonical_pages:
        canonical_key = _normalise_url(canonical)
        url_key = _normalise_url(url)
        if canonical_key is not None and url_key is not None:
            canonical_targets.setdefault(canonical_key, set()).add(url_key)
    canonical_collisions = [urls for urls in canonical_targets.values() if len(urls) > 1]
    if malformed_canonicals:
        issues.append(f"{malformed_canonicals} sampled page(s) publish a malformed canonical URL.")
    if off_host_canonicals:
        issues.append(f"{off_host_canonicals} sampled page(s) publish an off-host canonical URL.")
    if non_self_canonicals:
        issues.append(f"{non_self_canonicals} sampled page(s) canonicalize to a different same-host URL.")
    if canonical_collisions:
        collision_count = sum(len(urls) for urls in canonical_collisions)
        issues.append(
            f"{collision_count} distinct sampled page(s) share a canonical target across "
            f"{len(canonical_collisions)} collision group(s)."
        )
    if canonical_pages and not malformed_canonicals and not off_host_canonicals and not non_self_canonicals:
        positives.append("Sampled canonical URLs are normalized self-canonicals.")

    placeholder_count = sum(_looks_placeholder(description) for _, description in descriptions)
    if placeholder_count:
        issues.append(f"{placeholder_count} sampled meta description(s) look like placeholder or boilerplate text.")

    overlap_checked = 0
    low_overlap = 0
    social_checked = 0
    social_mismatches = 0
    stale_year_conflicts = 0
    for page in pages:
        supporting_text = " ".join(part for part in (page.title, page.h1, page.main_text) if part)
        visible_years = _years(supporting_text)
        metadata_values = tuple(
            value
            for value in (
                page.meta_description,
                page.open_graph_description,
                page.twitter_description,
            )
            if value
        )
        metadata_year_sets = tuple(_years(value) for value in metadata_values)
        if visible_years and any(
            years and max(years) < max(visible_years) for years in metadata_year_sets
        ):
            stale_year_conflicts += 1
        if not page.meta_description:
            continue
        coverage = _token_coverage(page.meta_description, supporting_text)
        if coverage is not None:
            overlap_checked += 1
            low_overlap += coverage < _LOW_OVERLAP_THRESHOLD
        for social_description in (page.open_graph_description, page.twitter_description):
            if social_description:
                social_checked += 1
                social_mismatches += _mismatched_social_description(page.meta_description, social_description)

    if low_overlap:
        issues.append(
            f"{low_overlap} sampled meta description(s) have low token overlap with the "
            "page title, H1, and main text."
        )
    elif overlap_checked:
        positives.append(f"{overlap_checked} sampled meta description(s) align with visible page content.")
    if stale_year_conflicts:
        issues.append(
            f"{stale_year_conflicts} sampled page(s) have an older year in page-owned description "
            "metadata than in visible title, H1, or main content."
        )
    if social_mismatches:
        issues.append(
            f"{social_mismatches} Open Graph/Twitter description(s) materially mismatch "
            "the page meta description."
        )
    elif social_checked:
        positives.append(
            f"{social_checked} Open Graph/Twitter description(s) are coherent with page "
            "meta descriptions."
        )

    missing_og = sum(page.open_graph_description is None for page in pages)
    missing_twitter = sum(page.twitter_description is None for page in pages)
    if missing_og:
        caveats.append(f"{missing_og} sampled page(s) omit the optional Open Graph description.")
    if missing_twitter:
        caveats.append(f"{missing_twitter} sampled page(s) omit the optional Twitter description.")

    return SearchBlurbAnalysis(pages, tuple(positives), tuple(issues), tuple(caveats))
