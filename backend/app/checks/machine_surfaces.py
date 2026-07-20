import asyncio
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.audit_context import AuditContext
from app.checks.ard_catalog import ard_catalog_quality
from app.checks.robots import nlweb_schemamap_found
from app.fetching import fetch_url, make_root_url
from app.models import CheckResult

_SURFACES = {
    "llms-full text export": ["/llms-full.txt"],
    "API description": ["/openapi.json", "/api/openapi.json", "/swagger.json"],
    "API catalog": ["/.well-known/api-catalog", "/.well-known/api-catalog.json", "/api-catalog.json"],
    "ARD catalog": ["/.well-known/ai-catalog.json"],
    "OAuth authorization metadata": [
        "/.well-known/oauth-authorization-server",
        "/.well-known/openid-configuration",
    ],
    "OAuth protected resource metadata": ["/.well-known/oauth-protected-resource"],
    "Web Bot Auth directory": ["/.well-known/http-message-signatures-directory"],
    "MCP server card": [
        "/.well-known/mcp/server-card.json",
        "/.well-known/mcp.json",
        "/.well-known/mcp-server",
        "/.well-known/mcp-server.json",
    ],
    "A2A agent card": ["/.well-known/agent-card.json", "/.well-known/a2a-agent.json", "/.well-known/agent.json"],
    "Agent skills": [
        "/.well-known/agent-skills/index.json",
        "/.well-known/agent-skills.json",
        "/.well-known/skills.json",
    ],
    "WebMCP manifest": ["/.well-known/webmcp.json", "/.well-known/webmcp"],
    "auth.md": ["/auth.md", "/.well-known/auth.md"],
    "agentic commerce metadata": [
        "/.well-known/x402",
        "/.well-known/x402.json",
        "/.well-known/mpp.json",
        "/.well-known/ucp.json",
        "/.well-known/acp.json",
        "/.well-known/payments.json",
    ],
    "content feed": ["/feed.xml", "/rss.xml", "/atom.xml"],
    "catalog JSON": ["/products.json", "/catalog.json", "/collections/all/products.json"],
    "legacy ai-plugin.json": ["/.well-known/ai-plugin.json"],
    "NLWeb /ask endpoint": ["/ask"],
}

_SURFACE_SCOPES = {
    "API description": "protocols",
    "API catalog": "protocols",
    "ARD catalog": "protocols",
    "OAuth authorization metadata": "account_auth",
    "OAuth protected resource metadata": "account_auth",
    "MCP server card": "protocols",
    "A2A agent card": "protocols",
    "Agent skills": "protocols",
    "WebMCP manifest": "protocols",
    "auth.md": "account_auth",
    "agentic commerce metadata": "commerce",
    "catalog JSON": "commerce",
    "legacy ai-plugin.json": "protocols",
    "NLWeb /ask endpoint": "protocols",
}

_LINK_HEADER_TOKENS = (
    "api-catalog",
    "oauth-authorization-server",
    "oauth-protected-resource",
    "mcp",
    "agent",
    "webmcp",
    "auth.md",
)

_DNS_AID_TOKENS = ("dns-aid", "agent", "mcp", "api-catalog", "llms")
_FEED_TYPES = {"application/rss+xml", "application/atom+xml", "application/feed+json"}
_MARKDOWN_TYPES = {"text/markdown", "text/plain"}

_CONVENTIONAL_PATHS = {
    "/docs": {"scope": "core", "label": "/docs documentation path"},
    "/pricing": {"scope": "commerce", "label": "/pricing path"},
    "/integrations": {"scope": "commerce", "label": "/integrations path"},
    "/api": {"scope": "protocols", "label": "/api overview path"},
}


def _looks_like_json(text: str) -> bool:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, (dict, list))


def _looks_like_feed(text: str) -> bool:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return False
    name = root.tag.rsplit("}", 1)[-1].lower()
    return name in {"rss", "feed", "rdf"}


def _is_valid_ai_plugin_shape(text: str) -> bool:
    """Validate legacy OpenAI-style ai-plugin.json descriptor shape.

    Returns True when the body parses as JSON dict AND either contains at
    least one recognized ai-plugin top-level field (``schema_version``,
    ``name_for_model``, ``name_for_human``, ``description_for_model``) or
    has an ``api`` sub-object with a ``url``. Malformed JSON or non-dict
    payloads are rejected.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    keys = {str(k).lower() for k in data}
    needed = {"schema_version", "name_for_model", "name_for_human", "description_for_model"}
    api = data.get("api")
    has_api = isinstance(api, dict) and "url" in api
    return bool(keys & needed) or has_api




def _nlweb_html_hints(context: AuditContext) -> bool:
    """Detect NLWeb hints in the homepage HTML.

    Looks for ``<meta name="nlweb">`` or any ``<link rel="...">`` whose
    ``rel`` value contains the substring ``nlweb``. Used as a tracked-only
    emerging signal — does not influence scoring.
    """
    if not context.homepage.ok:
        return False
    try:
        soup = BeautifulSoup(context.homepage.text, "lxml")
    except Exception:
        return False
    if soup.find("meta", attrs={"name": "nlweb"}):
        return True
    if soup.find("link", rel=lambda v: v and "nlweb" in v.lower()):
        return True
    return False


def _is_valid_surface(surface_type: str, text: str) -> bool:
    if len(text.strip()) < 20:
        return False
    if surface_type == "auth.md":
        return text.lstrip().startswith("#") or "authorization" in text.lower()
    if surface_type in {
        "API description",
        "API catalog",
        "ARD catalog",
        "OAuth authorization metadata",
        "OAuth protected resource metadata",
        "Web Bot Auth directory",
        "MCP server card",
        "A2A agent card",
        "Agent skills",
        "WebMCP manifest",
        "agentic commerce metadata",
        "catalog JSON",
        "legacy ai-plugin.json",
    }:
        if surface_type == "legacy ai-plugin.json":
            return _is_valid_ai_plugin_shape(text)
        return _looks_like_json(text)
    if surface_type == "content feed":
        return _looks_like_feed(text)
    return True


def _link_header_surfaces(context: AuditContext) -> list[str]:
    link_header = context.homepage.headers.get("link", "") if context.homepage.ok else ""
    lowered = link_header.lower()
    if any(token in lowered for token in _LINK_HEADER_TOKENS):
        return ["Link header discovery"]
    return []


def _alternate_link_surfaces(soup: BeautifulSoup, homepage_ok: bool) -> list[str]:
    if not homepage_ok:
        return []
    found: list[str] = []
    for tag in soup.find_all("link", rel=lambda value: value and "alternate" in value):
        link_type = tag.get("type", "").lower()
        href = tag.get("href")
        if not href:
            continue
        if link_type in _FEED_TYPES and "linked content feed" not in found:
            found.append("linked content feed")
        if link_type in _MARKDOWN_TYPES and "linked Markdown alternate" not in found:
            found.append("linked Markdown alternate")
    return found


def _linked_from_homepage(surface_url: str, soup: BeautifulSoup) -> bool:
    """Return whether a homepage anchor or link references the surface URL."""
    target = urlparse(surface_url)
    target_path = target.path.rstrip("/") or "/"
    root_url = f"{target.scheme}://{target.netloc}/" if target.netloc else "/"
    for tag in soup.find_all(["a", "link"], href=True):
        href = str(tag.get("href", "")).strip()
        if not href:
            continue
        candidate = urlparse(make_root_url(root_url, href))
        candidate_path = candidate.path.rstrip("/") or "/"
        if target.hostname and candidate.hostname != target.hostname:
            continue
        if candidate_path == target_path:
            return True
    return False


def _linked_from_link_header(surface_url: str, link_header: str) -> bool:
    """Return whether a Link header URI-reference matches the surface URL."""
    if not link_header:
        return False
    target = urlparse(surface_url)
    target_path = target.path.rstrip("/") or "/"
    root_url = f"{target.scheme}://{target.netloc}/" if target.netloc else "/"
    for reference in re.findall(r"<\s*([^>]+?)\s*>", link_header):
        candidate = urlparse(make_root_url(root_url, reference))
        candidate_path = candidate.path.rstrip("/") or "/"
        if target.hostname and candidate.hostname != target.hostname:
            continue
        if candidate_path == target_path:
            return True
    return False


def _surface_reachability(
    discovered_paths: set[str],
    soup: BeautifulSoup | None,
    link_header: str,
    context: AuditContext,
    extra_linked: set[str] | None = None,
) -> str:
    """Return orphaned-surface note or empty string.

    A discovered surface is orphaned if it is not linked from the
    homepage (<a href> or <link href>), not referenced in the homepage
    Link response header, and not linked from any extra reachability
    surface (for example the `/docs` page) supplied via
    ``extra_linked``.
    """
    if not discovered_paths or not soup:
        return ""
    extra_linked = extra_linked or set()
    orphaned: list[str] = []
    for path in sorted(discovered_paths):
        if path in extra_linked:
            continue
        surface_url = make_root_url(context.url, path)
        if _linked_from_homepage(surface_url, soup) or _linked_from_link_header(
            surface_url, link_header
        ):
            continue
        orphaned.append(path)
    if not orphaned:
        return ""
    return (
        " Reachability note: "
        + ", ".join(orphaned)
        + " exist but are not linked from the homepage or Link headers"
        + " — agents may not discover them."
    )


async def _probe_conventional_paths(
    context: AuditContext,
    include_protocols: bool,
    include_ecommerce: bool,
) -> str:
    """Probe conventional documentation/commerce/protocol paths.

    All four paths are probed unconditionally. Their scope tags determine
    how missing results are caveated in finding text, not whether they
    are probed. No surface failure here affects score, state, or fix.
    """
    paths = list(_CONVENTIONAL_PATHS.keys())
    responses = await asyncio.gather(
        *[fetch_url(make_root_url(context.url, path)) for path in paths]
    )
    found_paths: list[str] = []
    missing: dict[str, str] = {}
    for path, response in zip(paths, responses):
        if response.ok and response.text.strip():
            found_paths.append(path)
        else:
            missing[path] = _CONVENTIONAL_PATHS[path]["scope"]

    fragments: list[str] = []
    if found_paths:
        fragments.append("Conventional paths found: " + ", ".join(found_paths) + ".")
    # Missing /api when protocols scope is excluded
    if "/api" in missing and not include_protocols:
        fragments.append("API path not checked (excluded by scope).")
        missing.pop("/api")
    # Missing /pricing or /integrations when ecommerce scope is excluded
    if (
        ("/pricing" in missing or "/integrations" in missing)
        and not include_ecommerce
    ):
        fragments.append(
            "Pricing and integrations paths not checked (excluded by scope)."
        )
        missing.pop("/pricing", None)
        missing.pop("/integrations", None)
    # Anything still missing has its scope enabled — caveat but do not penalize.
    if missing:
        missing_list = ", ".join(sorted(missing.keys()))
        fragments.append(
            f"{missing_list} not found (site may not expose these — not penalized)."
        )
    return (" " + " ".join(fragments)) if fragments else ""


async def _docs_page_reachability(
    discovered_paths: set[str],
    context: AuditContext,
) -> set[str]:
    """Return the subset of ``discovered_paths`` reachable from /docs.

    Probes ``/docs`` once and parses any ``<a href>`` / ``<link href>``
    on the page. Returns an empty set if the page is missing, fetch
    fails, or no surface matches.
    """
    if not discovered_paths:
        return set()
    try:
        docs_response = await fetch_url(make_root_url(context.url, "/docs"))
    except Exception:
        return set()
    if not docs_response.ok or not docs_response.text.strip():
        return set()
    try:
        docs_soup = BeautifulSoup(docs_response.text, "lxml")
    except Exception:
        return set()
    if docs_soup is None:
        return set()
    reachable: set[str] = set()
    for tag in docs_soup.find_all(["a", "link"], href=True):
        href = str(tag.get("href", "")).strip()
        if not href:
            continue
        candidate = urlparse(make_root_url(context.url, href))
        candidate_path = candidate.path.rstrip("/") or "/"
        for path in discovered_paths:
            surface_url = make_root_url(context.url, path)
            target = urlparse(surface_url)
            target_path = target.path.rstrip("/") or "/"
            if (
                target.hostname
                and candidate.hostname
                and candidate.hostname != target.hostname
            ):
                continue
            if candidate_path == target_path:
                reachable.add(path)
    return reachable


async def _dns_aid_surfaces(context: AuditContext) -> list[str]:
    try:
        import dns.asyncresolver
        import dns.exception
    except ImportError:
        return []

    hostname = urlparse(context.url).hostname
    if not hostname:
        return []

    candidates = [hostname.removeprefix("www."), f"_agent.{hostname.removeprefix('www.')}", f"_dns-aid.{hostname.removeprefix('www.')}"]
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = 2
    resolver.timeout = 2

    for candidate in candidates:
        try:
            answers = await resolver.resolve(candidate, "TXT")
        except (dns.exception.Timeout, dns.resolver.NoNameservers, dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            continue
        except Exception:
            continue
        for answer in answers:
            text = " ".join(part.decode("utf-8", errors="ignore") for part in answer.strings).lower()
            if any(token in text for token in _DNS_AID_TOKENS):
                return ["DNS-AID TXT discovery"]
    return []


def _ard_robots_agentmap(context: AuditContext) -> bool:
    if not context.robots.ok:
        return False
    for raw_line in context.robots.text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key = line.split(":", 1)[0].strip().lower()
        if key == "agentmap":
            return True
    return False


def _ard_html_link(soup: BeautifulSoup | None) -> bool:
    if not soup:
        return False
    for tag in soup.find_all("link", rel=lambda value: value and "ai-catalog" in value):
        if tag.get("href"):
            return True
    return False


async def _ard_dns_hints(context: AuditContext) -> list[str]:
    try:
        import dns.asyncresolver
        import dns.exception
    except ImportError:
        return []

    hostname = urlparse(context.url).hostname
    if not hostname or not hostname.strip("."):
        return []

    candidates = [
        f"_catalog._agents.{hostname.removeprefix('www.')}",
        f"_search._agents.{hostname.removeprefix('www.')}",
    ]
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = 2
    resolver.timeout = 2
    found: list[str] = []

    for candidate in candidates:
        for record_type in ("TXT", "SRV"):
            try:
                answers = await resolver.resolve(candidate, record_type)
            except (dns.exception.Timeout, dns.resolver.NoNameservers, dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
                continue
            except Exception:
                continue
            if answers:
                found.append(f"ARD DNS {record_type} record")
                break
    return found


def _ard_catalog_detail(quality: dict | None) -> str:
    if not quality:
        return ""
    if not quality.get("valid"):
        sample = quality.get("sample_issues") or quality.get("issues") or []
        if sample:
            return " ARD catalog: present but failed validation (" + "; ".join(str(item) for item in sample) + ")."
        return " ARD catalog: present but failed validation."
    spec_version = quality.get("spec_version") or "unknown"
    entry_count = int(quality.get("entry_count") or 0)
    trust_manifest = "present" if quality.get("has_trust_manifest") else "missing"
    return (
        f" ARD catalog: {entry_count} entr{'y' if entry_count == 1 else 'ies'}, "
        f"spec v{spec_version}, trust manifest {trust_manifest}."
    )


def _surface_included(surface_type: str, include_protocols: bool, include_account_auth: bool, include_ecommerce: bool) -> bool:
    scope = _SURFACE_SCOPES.get(surface_type, "core")
    if scope == "protocols":
        return include_protocols
    if scope == "account_auth":
        return include_account_auth
    if scope == "commerce":
        return include_ecommerce
    return True


def _scoped_surfaces(
    include_protocols: bool,
    include_account_auth: bool,
    include_ecommerce: bool,
) -> dict[str, list[str]]:
    return {
        surface_type: paths
        for surface_type, paths in _SURFACES.items()
        if _surface_included(surface_type, include_protocols, include_account_auth, include_ecommerce)
    }


async def check_machine_surfaces(
    context: AuditContext,
    include_protocols: bool = False,
    include_account_auth: bool = False,
    include_ecommerce: bool = False,
) -> CheckResult:
    """Discover optional low-friction machine and agent protocol surfaces."""
    soup = BeautifulSoup(context.homepage.text, "lxml") if context.homepage.ok else None
    found: list[str] = _link_header_surfaces(context)
    if soup:
        found.extend(_alternate_link_surfaces(soup, context.homepage.ok))
    dns_aid_results, ard_dns_results = await asyncio.gather(
        _dns_aid_surfaces(context),
        _ard_dns_hints(context),
    )
    found.extend(dns_aid_results)
    found.extend(ard_dns_results)
    if _ard_robots_agentmap(context):
        found.append("ARD Agentmap robots directive")
    if _ard_html_link(soup):
        found.append("ARD HTML link discovery")
    if nlweb_schemamap_found(context.robots.text):
        found.append("NLWeb Schemamap robots directive")
    if _nlweb_html_hints(context):
        found.append("NLWeb HTML meta/link hint")
    surfaces = _scoped_surfaces(include_protocols, include_account_auth, include_ecommerce)
    # ``+2`` accounts for the two non-surface discovery families that
    # `check_machine_surfaces` always reports: link-header discovery and
    # alternate-link discovery. Adding/removing entries in ``_SURFACES``
    # already adjusts ``len(surfaces)``; the explicit ``+2`` would otherwise
    # double-count the constant families.
    checked_count = len(surfaces) + 2

    all_paths = [(surface_type, path) for surface_type, paths in surfaces.items() for path in paths]
    responses = await asyncio.gather(
        *[fetch_url(make_root_url(context.url, path)) for _, path in all_paths]
    )
    seen: set[str] = set()
    for (surface_type, _), response in zip(all_paths, responses):
        if surface_type == "NLWeb /ask endpoint":
            # /ask is intentionally probed with HEAD only — never POST.
            # See ``_probe_nlweb_ask`` below.
            continue
        if surface_type not in seen and response.ok and _is_valid_surface(surface_type, response.text):
            found.append(surface_type)
            seen.add(surface_type)

    discovered_paths: set[str] = set()
    for (surface_type, path), response in zip(all_paths, responses):
        if surface_type == "NLWeb /ask endpoint":
            continue
        if response.ok and _is_valid_surface(surface_type, response.text):
            discovered_paths.add(path)

    # HEAD probe for NLWeb /ask endpoint (tracked-only, no score impact).
    if "NLWeb /ask endpoint" in surfaces:
        try:
            ask_response = await fetch_url(
                make_root_url(context.url, "/ask"), method="HEAD"
            )
        except Exception:
            ask_response = None
        if ask_response is not None and ask_response.ok:
            found.append("NLWeb /ask endpoint")
            discovered_paths.add("/ask")

    ard_quality: dict | None = None
    for (surface_type, _), response in zip(all_paths, responses):
        if surface_type == "ARD catalog" and response.ok and response.text.strip():
            ard_quality = ard_catalog_quality(response.text, context.url)
            break

    ard_detail = _ard_catalog_detail(ard_quality)

    score = min(len(found), 3)

    if score >= 3:
        state = "pass"
        finding = (
            "Found multiple agent or machine-readable discovery surfaces: "
            + ", ".join(found)
            + "."
            + ard_detail
        )
        fix = "No action needed. These surfaces reduce extraction cost for agents and crawlers."
    elif score:
        state = "partial"
        excluded_scopes = []
        if not include_protocols:
            excluded_scopes.append("API/protocol surfaces")
        if not include_account_auth:
            excluded_scopes.append("account/auth surfaces")
        if not include_ecommerce:
            excluded_scopes.append("commerce surfaces")
        exclusion_detail = (
            " " + ", ".join(excluded_scopes) + " were excluded by audit scope."
            if excluded_scopes
            else ""
        )
        finding = (
            "Found limited agent or machine-readable discovery surfaces: "
            + ", ".join(found)
            + f". Essentials checked {checked_count} discovery families selected for this audit.{exclusion_detail}"
            + ard_detail
        )
        fix = (
            "Adopt the discovery standards that fit your site. Most are optional emerging standards, "
            "but feed, bot auth, API, account, protocol, or commerce surfaces can reduce agent integration cost when relevant."
        )
    else:
        state = "warn"
        recommended_surfaces = ["/llms-full.txt", "feed XML", "Web Bot Auth directory"]
        if include_protocols:
            recommended_surfaces.extend(["OpenAPI/API Catalog", "MCP/A2A manifests", "Agent Skills", "WebMCP", "ARD ai-catalog.json"])
        if include_account_auth:
            recommended_surfaces.extend(["OAuth metadata", "auth.md"])
        if include_ecommerce:
            recommended_surfaces.extend(["commerce protocol metadata", "catalog JSON"])
        finding = (
            f"No optional agent protocol or machine-readable surfaces were found across {checked_count} "
            "included discovery families. This is an agent-readiness opportunity, not proof that the "
            "public site cannot be crawled."
            + ard_detail
        )
        fix = f"Expose the lowest-friction surface that matches your business: {', '.join(recommended_surfaces)}."

    link_header = context.homepage.headers.get("link", "") if context.homepage.ok else ""
    docs_linked = await _docs_page_reachability(discovered_paths, context)
    finding += _surface_reachability(
        discovered_paths, soup, link_header, context, extra_linked=docs_linked
    )
    finding += await _probe_conventional_paths(
        context, include_protocols, include_ecommerce
    )

    return CheckResult(
        pillar="scrapability",
        check_name="machine_surfaces",
        label="Agent Protocol Discovery",
        state=state,
        evidence_level="inferred",
        score=score,
        max_score=3,
        finding=finding,
        fix=fix,
        effort="medium",
    )
