import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.audit_context import AuditContext
from app.checks.schema_ld import _extract_schemas, _normalise_types
from app.models import CheckResult

_ENTITY_SCHEMA_TYPES = {
    "Article",
    "LocalBusiness",
    "Organization",
    "Product",
    "SoftwareApplication",
    "WebSite",
}
_PLATFORMS = {
    "Instagram": re.compile(r"instagram\.com/[\w.\-]+", re.I),
    "TikTok": re.compile(r"tiktok\.com/@[\w.\-]+", re.I),
    "Facebook": re.compile(r"facebook\.com/[\w.\-]+", re.I),
    "X/Twitter": re.compile(r"(twitter\.com|x\.com)/[\w.\-]+", re.I),
    "YouTube": re.compile(r"youtube\.com/(channel/|c/|user/|@)[\w.\-]+", re.I),
    "LinkedIn": re.compile(r"linkedin\.com/company/", re.I),
    "Pinterest": re.compile(r"pinterest\.com/[\w.\-]+", re.I),
    "Reddit": re.compile(r"reddit\.com/(r|user)/", re.I),
    "Medium": re.compile(r"medium\.com/@?[\w\-]+", re.I),
    "Substack": re.compile(r"[\w\-]+\.substack\.com", re.I),
}
_STOP_WORDS = {
    "about",
    "and",
    "are",
    "for",
    "from",
    "home",
    "into",
    "official",
    "our",
    "that",
    "the",
    "this",
    "with",
    "your",
}

_TEMPLATES = {
    "pass": (
        "Homepage exposes {count} tracked profile signal(s): {platforms}. "
        "Open Graph, Twitter card, canonical, schema name, image/logo, and sameAs "
        "metadata are consistent enough for crawlers to connect the page to its entity.",
        "No action needed. Keep share metadata, schema names, images, and official "
        "profile links aligned as the site changes.",
    ),
    "partial": (
        "Homepage has usable social/entity signals, but {issues}. Detected tracked "
        "profile signal(s): {found}. Positive metadata signals: {positives}.",
        "Align Open Graph, Twitter card, canonical URL, JSON-LD entity names, logo/image "
        "fields, and Organization schema sameAs values. If profiles are intentionally "
        "unlinked, no broader social-presence fix is implied.",
    ),
    "not_linked": (
        "Homepage exposes weak social/entity metadata: {issues}. This is only an on-site "
        "metadata signal, not proof that the brand lacks off-site presence.",
        "Publish consistent Open Graph and Twitter card tags, align them with the canonical "
        "URL and JSON-LD entity name, and expose official profiles through links or "
        "Organization schema sameAs values.",
    ),
    "fetch_error": (
        "Could not fetch homepage to check social and entity metadata.",
        "Ensure the homepage is publicly accessible.",
    ),
}


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split()).strip()


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2 and token not in _STOP_WORDS
    }


def _texts_align(first: str | None, second: str | None) -> bool:
    first_text = _clean_text(first).lower()
    second_text = _clean_text(second).lower()
    if not first_text or not second_text:
        return False
    if first_text == second_text:
        return True
    if min(len(first_text), len(second_text)) >= 4 and (
        first_text in second_text or second_text in first_text
    ):
        return True

    first_tokens = _tokens(first_text)
    second_tokens = _tokens(second_text)
    if not first_tokens or not second_tokens:
        return False
    return len(first_tokens & second_tokens) / min(len(first_tokens), len(second_tokens)) >= 0.5


def _meta_content(soup: BeautifulSoup, attr: str, key: str) -> str | None:
    for tag in soup.find_all("meta"):
        if str(tag.get(attr, "")).lower() != key.lower():
            continue
        content = _clean_text(tag.get("content"))
        if content:
            return content
    return None


def _link_href(soup: BeautifulSoup, rel: str) -> str | None:
    for tag in soup.find_all("link", href=True):
        rel_value = tag.get("rel", [])
        rel_tokens = rel_value.split() if isinstance(rel_value, str) else [str(item) for item in rel_value]
        if rel.lower() in {token.lower() for token in rel_tokens}:
            return _clean_text(tag.get("href"))
    return None


def _normalised_url(value: str | None, base_url: str) -> str | None:
    if not value:
        return None
    parsed = urlparse(urljoin(base_url, value))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    path = parsed.path.rstrip("/") or "/"
    return parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=path,
        fragment="",
    ).geturl()


def _schema_url_values(value) -> list[str]:
    if isinstance(value, str):
        cleaned = _clean_text(value)
        return [cleaned] if cleaned else []
    if isinstance(value, dict):
        urls: list[str] = []
        for key in ("url", "contentUrl", "@id"):
            urls.extend(_schema_url_values(value.get(key)))
        return urls
    if isinstance(value, list):
        urls: list[str] = []
        for item in value:
            urls.extend(_schema_url_values(item))
        return urls
    return []


def _schema_entity_signals(soup: BeautifulSoup) -> tuple[list[str], list[str], list[str]]:
    names: list[str] = []
    same_as_links: list[str] = []
    image_links: list[str] = []

    for schema in _extract_schemas(soup)[0]:
        schema_types = set(_normalise_types(schema.get("@type")))
        if not schema_types & _ENTITY_SCHEMA_TYPES:
            continue

        name = schema.get("name") or schema.get("headline")
        if isinstance(name, str) and _clean_text(name):
            names.append(_clean_text(name))

        same_as = schema.get("sameAs")
        if isinstance(same_as, str):
            same_as_links.append(same_as)
        elif isinstance(same_as, list):
            same_as_links.extend(item for item in same_as if isinstance(item, str))

        image_links.extend(_schema_url_values(schema.get("logo")))
        image_links.extend(_schema_url_values(schema.get("image")))

    return _dedupe(names), _dedupe(same_as_links), _dedupe(image_links)


def _dedupe(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        normalised = _clean_text(value)
        if normalised and normalised.lower() not in {item.lower() for item in cleaned}:
            cleaned.append(normalised)
    return cleaned


def _platforms_for_links(links: list[str]) -> list[str]:
    return [name for name, pattern in _PLATFORMS.items() if any(pattern.search(link) for link in links)]


def _format_list(values: list[str], limit: int = 4) -> str:
    if not values:
        return "none"
    visible = values[:limit]
    suffix = f"; {len(values) - limit} more" if len(values) > limit else ""
    return "; ".join(visible) + suffix


def _metadata_consistency(
    soup: BeautifulSoup,
    base_url: str,
    homepage_links: list[str],
    schema_names: list[str],
    same_as_links: list[str],
    schema_image_links: list[str],
) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    positives: list[str] = []

    title_tag = soup.find("title")
    page_title = _clean_text(title_tag.get_text(" ", strip=True) if title_tag else None)
    description = _meta_content(soup, "name", "description")
    canonical_url = _normalised_url(_link_href(soup, "canonical"), base_url)

    og_title = _meta_content(soup, "property", "og:title")
    og_description = _meta_content(soup, "property", "og:description")
    og_url = _normalised_url(_meta_content(soup, "property", "og:url"), base_url)
    og_site_name = _meta_content(soup, "property", "og:site_name")
    og_image = _normalised_url(_meta_content(soup, "property", "og:image"), base_url)

    twitter_title = _meta_content(soup, "name", "twitter:title")
    twitter_description = _meta_content(soup, "name", "twitter:description")
    twitter_image = _normalised_url(_meta_content(soup, "name", "twitter:image"), base_url)
    twitter_card = _meta_content(soup, "name", "twitter:card")

    if og_title and page_title and _texts_align(og_title, page_title):
        positives.append("og:title aligns with page title")
    elif og_title and page_title:
        issues.append("og:title does not align with page title")
    else:
        issues.append("missing og:title or page title")

    if og_description and description and _texts_align(og_description, description):
        positives.append("og:description aligns with meta description")
    elif og_description and description:
        issues.append("og:description does not align with meta description")
    else:
        issues.append("missing og:description or meta description")

    if og_url and canonical_url and og_url == canonical_url:
        positives.append("og:url matches canonical URL")
    elif og_url and canonical_url:
        issues.append("og:url does not match canonical URL")
    elif og_url:
        positives.append("og:url is present")
    else:
        issues.append("missing og:url")

    if twitter_card or twitter_title or twitter_description or twitter_image:
        positives.append("Twitter card metadata is present")
        if twitter_title and og_title and not _texts_align(twitter_title, og_title):
            issues.append("twitter:title does not align with og:title")
        if twitter_description and (og_description or description) and not _texts_align(
            twitter_description,
            og_description or description,
        ):
            issues.append("twitter:description does not align with page description")
    else:
        issues.append("no Twitter card metadata found")

    social_images = [url for url in [og_image, twitter_image] if url]
    if social_images:
        positives.append("social image metadata is present")
    else:
        issues.append("missing og:image or twitter:image")
    if schema_image_links:
        positives.append("schema logo/image metadata is present")
    else:
        issues.append("no schema logo/image found for entity media cross-check")

    entity_name_candidates = [candidate for candidate in [og_site_name, og_title, page_title] if candidate]
    if schema_names and any(
        _texts_align(schema_name, candidate)
        for schema_name in schema_names
        for candidate in entity_name_candidates
    ):
        positives.append("schema entity name aligns with page/social metadata")
    elif schema_names:
        issues.append("schema entity names do not align with page or social metadata")
    else:
        issues.append("no schema entity name available for metadata cross-check")

    linked_platforms = set(_platforms_for_links(homepage_links))
    same_as_platforms = set(_platforms_for_links(same_as_links))
    if linked_platforms and same_as_platforms and linked_platforms & same_as_platforms:
        positives.append("schema sameAs overlaps visible profile links")
    elif linked_platforms and same_as_platforms:
        issues.append("visible profile links and schema sameAs point to different tracked platforms")
    elif same_as_platforms:
        positives.append("schema sameAs exposes tracked profiles")
    elif linked_platforms:
        issues.append("visible profile links are not mirrored in schema sameAs")
    else:
        issues.append("no tracked profile links or schema sameAs values found")

    return issues, positives


async def check_social(context: AuditContext) -> CheckResult:
    """Detect homepage profile links and entity metadata consistency."""
    if not context.homepage.ok:
        finding, fix = _TEMPLATES["fetch_error"]
        return CheckResult(
            pillar="off_site",
            check_name="social",
            label="Social & Entity Metadata",
            state="warn",
            evidence_level="unknown",
            score=0,
            max_score=2,
            finding=finding,
            fix=fix,
            effort="low",
        )

    soup = BeautifulSoup(context.homepage.text, "lxml")
    homepage_links = [a.get("href", "") for a in soup.find_all("a", href=True)]
    schema_names, same_as_links, schema_image_links = _schema_entity_signals(soup)
    all_links = [*homepage_links, *same_as_links]
    found = _platforms_for_links(all_links)
    issues, positives = _metadata_consistency(
        soup,
        context.homepage.final_url,
        homepage_links,
        schema_names,
        same_as_links,
        schema_image_links,
    )

    count = len(found)

    if count >= 3 and not issues:
        state = "pass"
        score = 2
        finding, fix = _TEMPLATES["pass"]
        finding = finding.format(count=count, platforms=", ".join(found))
    elif count > 0 or len(positives) >= 3:
        state = "partial"
        score = 1
        finding, fix = _TEMPLATES["partial"]
        finding = finding.format(
            issues=_format_list(issues),
            found=", ".join(found) if found else "none",
            positives=_format_list(positives),
        )
    else:
        state = "warn"
        score = 0
        finding, fix = _TEMPLATES["not_linked"]
        finding = finding.format(issues=_format_list(issues))

    return CheckResult(
        pillar="off_site",
        check_name="social",
        label="Social & Entity Metadata",
        state=state,
        evidence_level="verified",
        score=score,
        max_score=2,
        finding=finding,
        fix=fix,
        effort="low",
    )
