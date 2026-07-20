import json
import os
from datetime import UTC
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.audit_context import AuditContext
from app.entity_cache import (
    CachedEntityLookup,
    entity_cache_ttl,
    get_cached_entity_lookup,
    set_cached_entity_lookup,
)
from app.models import CheckResult

_WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
_WIKIMEDIA_USER_AGENT = os.getenv(
    "WIKIMEDIA_USER_AGENT",
    "MachineRead/0.1 (https://machineread.ai; support@machineread.ai)",
)

_TEMPLATES = {
    "both": (
        "Brand entity found in both Wikipedia and Wikidata. This is a strong "
        "earned entity signal for LLM recall and retrieval.",
        "Maintain the Wikipedia article's quality and keep Wikidata facts current.",
    ),
    "wikidata_only": (
        "Brand entity found in Wikidata but no matching Wikipedia article was found.",
        "Consider Wikipedia only if the brand meets notability guidelines. Earned "
        "coverage comes before the article.",
    ),
    "wikipedia_only": (
        "Wikipedia article found but no matching Wikidata entity was confirmed.",
        "Check whether the article has a linked Wikidata item and official website.",
    ),
    "neither": (
        "No Wikipedia article or Wikidata entity was confirmed for the detected brand candidates.",
        "For small brands, this may be normal. Build third-party citations, press, "
        "reviews, and community mentions before pursuing knowledge graph presence.",
    ),
    "fetch_error": (
        "Could not query Wikipedia or Wikidata APIs.",
        "These are public entity APIs. Try again later if the issue persists.",
    ),
    "refused": (
        "Wikimedia refused or throttled the public entity lookup ({reason}). "
        "This is not treated as proof that the brand lacks Wikipedia or Wikidata presence.",
        "No site change is required. MachineRead will use the cached refusal window "
        "instead of retrying this public API immediately.",
    ),
}


def _domain_candidate(url: str) -> str:
    hostname = urlparse(url).hostname or ""
    parts = hostname.split(".")
    candidates = [part for part in parts if part not in {"www", "com", "net", "org", "io", "co"}]
    return candidates[0] if candidates else hostname


def _cache_key(context: AuditContext) -> str:
    hostname = urlparse(context.homepage.final_url if context.homepage.ok else context.url).hostname or ""
    return "v2:" + hostname.removeprefix("www.").lower()


def _cached_finding(cached: CachedEntityLookup) -> str:
    checked_on = cached.checked_at.astimezone(UTC).date().isoformat()
    ttl_days = entity_cache_ttl().days
    return (
        f"{cached.finding} Cached entity lookup from {checked_on}; no public "
        f"Wikimedia API call was made because the cache is under {ttl_days} days old."
    )


def _result_from_cache(cached: CachedEntityLookup) -> CheckResult:
    return CheckResult(
        pillar="off_site",
        check_name="wikipedia",
        label="Wikipedia & Wikidata Entity",
        state=cached.state,
        evidence_level=cached.evidence_level,
        score=cached.score,
        max_score=cached.max_score,
        finding=_cached_finding(cached),
        fix=cached.fix,
        effort="high",
    )


def _cache_result(cache_key: str, status: str, result: CheckResult) -> CheckResult:
    set_cached_entity_lookup(
        cache_key=cache_key,
        status=status,
        state=result.state,
        evidence_level=result.evidence_level,
        score=result.score,
        max_score=result.max_score,
        finding=result.finding,
        fix=result.fix,
    )
    ttl_days = entity_cache_ttl().days
    result.finding = f"{result.finding} This result is now cached for {ttl_days} days."
    return result


def _schema_names(soup: BeautifulSoup) -> list[str]:
    names: list[str] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data.get("@graph", []) if isinstance(data, dict) else data
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("@type") in {"Organization", "LocalBusiness", "WebSite"}:
                name = item.get("name")
                if isinstance(name, str):
                    names.append(name)
    return names


def _brand_candidates(context: AuditContext) -> list[str]:
    candidates: list[str] = []
    if context.homepage.ok:
        soup = BeautifulSoup(context.homepage.text, "lxml")
        og_site = soup.find("meta", attrs={"property": "og:site_name"})
        if og_site and og_site.get("content"):
            candidates.append(og_site["content"])
        candidates.extend(_schema_names(soup))
        title = soup.find("title")
        if title and title.get_text(strip=True):
            candidates.append(title.get_text(" ", strip=True).split("|")[0].split(" - ")[0])

    candidates.append(_domain_candidate(context.url))

    cleaned: list[str] = []
    for candidate in candidates:
        value = " ".join(candidate.split()).strip()
        if value and value.lower() not in {item.lower() for item in cleaned}:
            cleaned.append(value)
    return cleaned[:4]


def _website_variants(context: AuditContext) -> list[str]:
    parsed = urlparse(context.homepage.final_url if context.homepage.ok else context.url)
    root = f"{parsed.scheme}://{parsed.netloc}/"
    no_slash = root.rstrip("/")
    variants = [root, no_slash]
    if parsed.hostname and parsed.hostname.startswith("www."):
        bare = parsed.hostname.removeprefix("www.")
        variants.append(f"{parsed.scheme}://{bare}/")
        variants.append(f"{parsed.scheme}://{bare}")
    elif parsed.hostname:
        variants.append(f"{parsed.scheme}://www.{parsed.hostname}/")
        variants.append(f"{parsed.scheme}://www.{parsed.hostname}")
    return variants


async def _has_wikidata_website(context: AuditContext, client: httpx.AsyncClient) -> bool:
    values = " ".join(f"<{variant}>" for variant in _website_variants(context))
    sparql = f"""
    SELECT ?item WHERE {{
        VALUES ?website {{ {values} }}
        ?item wdt:P856 ?website .
    }} LIMIT 1
    """
    response = await client.get(
        _WIKIDATA_SPARQL,
        params={"query": sparql, "format": "json"},
        headers={"User-Agent": _WIKIMEDIA_USER_AGENT},
    )
    response.raise_for_status()
    bindings = response.json().get("results", {}).get("bindings", [])
    return bool(bindings)


async def _has_wikidata_label(candidates: list[str], client: httpx.AsyncClient) -> bool:
    for candidate in candidates:
        candidate_key = candidate.lower()
        response = await client.get(
            _WIKIDATA_API,
            params={
                "action": "wbsearchentities",
                "search": candidate,
                "language": "en",
                "format": "json",
                "limit": 3,
            },
            headers={"User-Agent": _WIKIMEDIA_USER_AGENT},
        )
        response.raise_for_status()
        results = response.json().get("search", [])
        if any(result.get("label", "").lower() == candidate_key for result in results):
            return True
    return False


async def _has_wikipedia(candidates: list[str], client: httpx.AsyncClient) -> bool:
    for candidate in candidates:
        candidate_key = candidate.lower()
        response = await client.get(
            _WIKIPEDIA_API,
            params={
                "action": "query",
                "list": "search",
                "srsearch": candidate,
                "format": "json",
                "srlimit": 3,
            },
            headers={"User-Agent": _WIKIMEDIA_USER_AGENT},
        )
        response.raise_for_status()
        results = response.json().get("query", {}).get("search", [])
        for result in results:
            title = result.get("title", "").lower()
            title_without_parenthetical = title.split(" (", 1)[0]
            if title in {candidate_key, candidate_key + ".com"}:
                return True
            if title_without_parenthetical == candidate_key:
                return True
    return False


async def check_wikipedia(context: AuditContext) -> CheckResult:
    """Check for brand presence in Wikipedia and Wikidata."""
    cache_key = _cache_key(context)
    cached = get_cached_entity_lookup(cache_key)
    if cached:
        return _result_from_cache(cached)

    candidates = _brand_candidates(context)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            has_wikidata = await _has_wikidata_website(context, client)
            if not has_wikidata:
                has_wikidata = await _has_wikidata_label(candidates, client)
            has_wikipedia = await _has_wikipedia(candidates, client)
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        template_key = "refused" if status_code in {403, 429} else "fetch_error"
        finding, fix = _TEMPLATES[template_key]
        if template_key == "fetch_error":
            result = CheckResult(
                pillar="off_site",
                check_name="wikipedia",
                label="Wikipedia & Wikidata Entity",
                state="warn",
                evidence_level="unknown",
                score=2,
                max_score=4,
                finding=f"{finding} Public API returned HTTP {status_code}.",
                fix=fix,
                effort="high",
            )
            return _cache_result(cache_key, "error", result)

        result = CheckResult(
            pillar="off_site",
            check_name="wikipedia",
            label="Wikipedia & Wikidata Entity",
            state="warn",
            evidence_level="unknown",
            score=2,
            max_score=4,
            finding=finding.format(reason=f"HTTP {status_code}"),
            fix=fix,
            effort="high",
        )
        return _cache_result(cache_key, "refused", result)
    except httpx.HTTPError:
        finding, fix = _TEMPLATES["fetch_error"]
        result = CheckResult(
            pillar="off_site",
            check_name="wikipedia",
            label="Wikipedia & Wikidata Entity",
            state="warn",
            evidence_level="unknown",
            score=2,
            max_score=4,
            finding=finding,
            fix=fix,
            effort="high",
        )
        return _cache_result(cache_key, "error", result)

    if has_wikidata and has_wikipedia:
        key, score, state = "both", 4, "pass"
    elif has_wikidata:
        key, score, state = "wikidata_only", 3, "partial"
    elif has_wikipedia:
        key, score, state = "wikipedia_only", 2, "partial"
    else:
        key, score, state = "neither", 0, "fail"

    finding, fix = _TEMPLATES[key]
    result = CheckResult(
        pillar="off_site",
        check_name="wikipedia",
        label="Wikipedia & Wikidata Entity",
        state=state,
        evidence_level="verified",
        score=score,
        max_score=4,
        finding=finding,
        fix=fix,
        effort="high",
    )
    return _cache_result(cache_key, key, result)
