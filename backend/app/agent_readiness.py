import asyncio
import json
from dataclasses import dataclass
from urllib.parse import urlparse

from app.audit_context import AuditContext
from app.benchmarks import build_agent_benchmark_comparison
from app.checks.llms_txt import agent_text_access, best_sitemap
from app.checks.robots import content_signal_tokens, explicit_ai_bot_count
from app.fetching import FetchResult, fetch_url, make_root_url
from app.models import AgentReadinessCategory, AgentReadinessSummary

_LINK_HEADER_TOKENS = (
    "api-catalog",
    "oauth-authorization-server",
    "oauth-protected-resource",
    "mcp",
    "agent",
    "webmcp",
    "auth.md",
)

_JSON_SURFACES: tuple[tuple[str, tuple[str, ...], str, str, str, tuple[str, ...]], ...] = (
    (
        "Web Bot Auth directory",
        ("/.well-known/http-message-signatures-directory",),
        "application/json, */*;q=0.5",
        "Bot Access Control",
        "core",
        ("keys",),
    ),
    (
        "API Catalog",
        ("/.well-known/api-catalog", "/.well-known/api-catalog.json", "/api-catalog.json"),
        "application/linkset+json, application/json, */*;q=0.5",
        "Protocol Discovery",
        "protocols",
        ("linkset", "api-catalog"),
    ),
    (
        "OAuth/OIDC discovery metadata",
        (
            "/.well-known/oauth-authorization-server",
            "/.well-known/openid-configuration",
        ),
        "application/json, */*;q=0.5",
        "Protocol Discovery",
        "account_auth",
        ("issuer",),
    ),
    (
        "OAuth Protected Resource metadata",
        ("/.well-known/oauth-protected-resource",),
        "application/json, */*;q=0.5",
        "Protocol Discovery",
        "account_auth",
        ("resource", "authorization_servers"),
    ),
    (
        "MCP Server Card",
        (
            "/.well-known/mcp/server-card.json",
            "/.well-known/mcp.json",
            "/.well-known/mcp/server-cards.json",
        ),
        "application/json, */*;q=0.5",
        "Protocol Discovery",
        "protocols",
        ("name",),
    ),
    (
        "A2A Agent Card",
        (
            "/.well-known/agent-card.json",
            "/.well-known/a2a-agent.json",
            "/.well-known/agent.json",
        ),
        "application/json, */*;q=0.5",
        "Protocol Discovery",
        "protocols",
        ("name",),
    ),
    (
        "Agent Skills index",
        (
            "/.well-known/agent-skills/index.json",
            "/.well-known/skills/index.json",
            "/.well-known/agent-skills.json",
        ),
        "application/json, */*;q=0.5",
        "Protocol Discovery",
        "protocols",
        ("skills",),
    ),
    (
        "WebMCP manifest",
        ("/.well-known/webmcp.json", "/.well-known/webmcp"),
        "application/json, */*;q=0.5",
        "Protocol Discovery",
        "protocols",
        ("name", "tools"),
    ),
    (
        "ARD static catalog",
        ("/.well-known/ai-catalog.json",),
        "application/json, */*;q=0.5",
        "Protocol Discovery",
        "protocols",
        ("specVersion", "entries"),
    ),
    (
        "x402 payment metadata",
        ("/.well-known/x402", "/.well-known/x402.json"),
        "application/json, */*;q=0.5",
        "Commerce",
        "commerce",
        ("accepts", "resource"),
    ),
    (
        "MPP commerce metadata",
        ("/.well-known/mpp.json",),
        "application/json, */*;q=0.5",
        "Commerce",
        "commerce",
        ("version", "manifest"),
    ),
    (
        "UCP commerce metadata",
        ("/.well-known/ucp.json",),
        "application/json, */*;q=0.5",
        "Commerce",
        "commerce",
        ("version", "manifest"),
    ),
    (
        "ACP commerce metadata",
        ("/.well-known/acp.json",),
        "application/json, */*;q=0.5",
        "Commerce",
        "commerce",
        ("version", "manifest"),
    ),
)

# Top-level keys that, taken alone, indicate a soft-404 / generic error
# response rather than a real discovery document. Bodies whose only
# top-level keys come from this set are rejected by ``_looks_like_json``
# to prevent SPA/CDN error pages from passing protocol probes.
_ERROR_ONLY_KEYS: frozenset[str] = frozenset({"error", "message", "status", "code"})

_AUTH_MD_PATHS = ("/auth.md", "/.well-known/auth.md")
_DNS_PREFIXES = ("_index._agents", "_a2a._agents", "_mcp._agents")
_DNS_TYPES = ("SVCB", "HTTPS", "TXT")


@dataclass(frozen=True)
class ProbeResult:
    label: str
    category: str
    passed: bool
    included: bool = True


def _looks_like_json(
    response: FetchResult,
    require_keys: list[str] | tuple[str, ...] | None = None,
) -> bool:
    """Decide whether ``response`` looks like a real JSON discovery document.

    Beyond the basic content-type + parseable-JSON gate, two shape checks
    protect against soft-404 bodies:

    1. **Error-only rejection** — if the parsed body is a dict whose
       top-level keys are all members of ``_ERROR_ONLY_KEYS`` (e.g.
       ``{"error": "not found"}``), the body is treated as a generic error
       envelope rather than a discovery document.
    2. **Surface-specific required keys** — when ``require_keys`` is
       supplied, the dict must contain at least one of those keys to be
       considered a valid document for the probed surface (e.g. an MCP
       card must have ``name``, OAuth metadata must have ``issuer``).
       JSON arrays and scalars are rejected when ``require_keys`` is set.
    """
    content_type = response.headers.get("content-type", "").lower()
    if "json" not in content_type and "linkset" not in content_type:
        return False
    try:
        parsed = json.loads(response.text)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, (dict, list)):
        return False
    if isinstance(parsed, list):
        # Lists cannot satisfy dict-shape requirements; only return True
        # when no shape validation was requested.
        return require_keys is None
    # Error-only rejection: every top-level key is a generic error field.
    if parsed and set(parsed).issubset(_ERROR_ONLY_KEYS):
        return False
    if require_keys:
        return any(key in parsed for key in require_keys)
    return True


def _looks_like_auth_md(response: FetchResult) -> bool:
    sample = response.text.strip()[:3000].lower()
    if len(sample) < 20:
        return False
    return response.text.lstrip().startswith("#") or ("auth" in sample and "agent" in sample)


def _has_agent_link_header(context: AuditContext) -> bool:
    if not context.homepage.ok:
        return False
    link_header = context.homepage.headers.get("link", "").lower()
    return any(token in link_header for token in _LINK_HEADER_TOKENS)


def _dns_candidates(context: AuditContext) -> list[str]:
    hostname = urlparse(context.url).hostname
    if not hostname:
        return []

    roots = []
    for root in (hostname, hostname.removeprefix("www.")):
        if root not in roots:
            roots.append(root)

    return [
        f"{prefix}.{root}"
        for root in roots
        for prefix in _DNS_PREFIXES
    ]


# RCODE values from dnspython that indicate the name does not exist.
# NXDOMAIN (3) means "no such name" — we can stop probing other record
# types and other prefixes for the same base name. Empty domain (5)
# similarly indicates an unreachable name and short-circuits cleanly.
_NXDOMAIN_RCODES = {3, 5}


async def _has_dns_aid(context: AuditContext) -> bool:
    try:
        import dns.asyncresolver
        import dns.exception
    except ImportError:
        return False

    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = 2
    resolver.timeout = 2

    for candidate in _dns_candidates(context):
        base_name = candidate.split(".", 1)[1] if "." in candidate else candidate
        for record_type in _DNS_TYPES:
            try:
                answers = await resolver.resolve(candidate, record_type)
            except dns.resolver.NoNameservers:
                # No working nameserver — treat as inconclusive, keep going.
                continue
            except dns.exception.Timeout:
                continue
            except dns.resolver.NXDOMAIN as exc:
                # NXDOMAIN on the base name → skip remaining record types
                # and prefixes rooted at the same name. Sub-prefix lookups
                # under an NXDOMAIN apex are guaranteed to fail.
                if base_name == candidate.split(".", 1)[1] and exc.rcode() in _NXDOMAIN_RCODES:
                    break
                continue
            except dns.resolver.NoAnswer:
                # Name exists but no records of this type — try next type.
                continue
            except Exception:
                continue
            if answers:
                return True
    return False


async def _json_surface_available(
    context: AuditContext,
    paths: tuple[str, ...],
    accept: str,
    require_keys: tuple[str, ...] | None = None,
) -> bool:
    responses = await asyncio.gather(
        *[fetch_url(make_root_url(context.url, path), accept=accept) for path in paths]
    )
    return any(
        response.ok and _looks_like_json(response, require_keys)
        for response in responses
    )


async def _auth_md_available(context: AuditContext) -> bool:
    responses = await asyncio.gather(
        *[
            fetch_url(make_root_url(context.url, path), accept="text/markdown, text/plain, */*;q=0.5")
            for path in _AUTH_MD_PATHS
        ]
    )
    return any(response.ok and _looks_like_auth_md(response) for response in responses)


def _scope_included(scope: str, include_protocols: bool, include_account_auth: bool, include_ecommerce: bool) -> bool:
    if scope == "core":
        return True
    if scope == "protocols":
        return include_protocols
    if scope == "account_auth":
        return include_account_auth
    if scope == "commerce":
        return include_ecommerce
    return False


# Core probes that are always part of the default-scope strict-agent lens
# and do not depend on any optional scope flag. These 7 names mirror the
# unconditional ``ProbeResult(...)`` entries constructed in
# ``build_agent_readiness_summary``. ``auth.md`` is added separately below
# because it is gated by ``include_account_auth`` via
# ``ProbeResult(included=include_account_auth)``.
_CORE_PROBES: tuple[str, ...] = (
    "robots.txt published",
    "valid sitemap discovery",
    "agent discovery Link headers",
    "DNS-AID records",
    "llms.txt or Markdown negotiation",
    "AI-specific robots.txt rules",
    "Content Signals in robots.txt",
)


def _scope_probe_labels(
    include_protocols: bool,
    include_account_auth: bool,
    include_ecommerce: bool,
) -> list[str]:
    """Enumerate the strict-agent probe labels for the resolved scope.

    Mirrors the probe list constructed by ``build_agent_readiness_summary``
    so consumers can rely on ``len(passed) + len(missing) == max`` in both
    the success and degraded-fallback paths.
    """
    labels: list[str] = list(_CORE_PROBES)
    # Web Bot Auth directory is the single core JSON surface.
    labels.extend(label for (label, _, _, _, scope, _) in _JSON_SURFACES if scope == "core")
    if include_account_auth:
        # The hardcoded ``auth.md`` probe is gated by include_account_auth
        # in ``build_agent_readiness_summary``.
        labels.append("auth.md")
    labels.extend(
        label
        for (label, _, _, _, scope, _) in _JSON_SURFACES
        if scope != "core"
        and _scope_included(scope, include_protocols, include_account_auth, include_ecommerce)
    )
    return labels


def _category_summaries(probes: list[ProbeResult]) -> list[AgentReadinessCategory]:
    categories = [
        "Discoverability",
        "Content Accessibility",
        "Bot Access Control",
        "Protocol Discovery",
        "Commerce",
    ]
    summaries: list[AgentReadinessCategory] = []
    for category in categories:
        category_probes = [probe for probe in probes if probe.category == category]
        included = [probe for probe in category_probes if probe.included]
        passed = [probe.label for probe in included if probe.passed]
        missing = [probe.label for probe in included if not probe.passed]
        excluded = [probe.label for probe in category_probes if not probe.included]
        earned = len(passed)
        maximum = len(included)
        score = round((earned / maximum) * 100) if maximum else 0
        summaries.append(
            AgentReadinessCategory(
                name=category,
                earned=earned,
                max=maximum,
                score=score,
                passed=passed,
                missing=missing,
                excluded=excluded,
            )
        )
    return summaries


async def build_agent_readiness_summary(
    context: AuditContext,
    include_protocols: bool = False,
    include_account_auth: bool = False,
    include_ecommerce: bool = False,
) -> AgentReadinessSummary:
    sitemap_valid, _, _, _, _ = await best_sitemap(context)
    text_access_available, _ = await agent_text_access(context)
    json_surface_results = [False for _ in _JSON_SURFACES]
    json_surface_tasks = []
    json_surface_indices = []
    for index, (_, paths, accept, _, scope, require_keys) in enumerate(_JSON_SURFACES):
        if _scope_included(scope, include_protocols, include_account_auth, include_ecommerce):
            json_surface_tasks.append(
                _json_surface_available(context, paths, accept, require_keys)
            )
            json_surface_indices.append(index)
    if json_surface_tasks:
        json_surface_found = await asyncio.gather(*json_surface_tasks)
        for index, found in zip(json_surface_indices, json_surface_found, strict=True):
            json_surface_results[index] = found

    auth_md_available = await _auth_md_available(context) if include_account_auth else False

    probes = [
        ProbeResult(
            "robots.txt published",
            "Discoverability",
            context.robots.ok and bool(context.robots.text.strip()),
        ),
        ProbeResult("valid sitemap discovery", "Discoverability", sitemap_valid),
        ProbeResult("agent discovery Link headers", "Discoverability", _has_agent_link_header(context)),
        ProbeResult("DNS-AID records", "Discoverability", await _has_dns_aid(context)),
        ProbeResult("llms.txt or Markdown negotiation", "Content Accessibility", text_access_available),
        ProbeResult(
            "AI-specific robots.txt rules",
            "Bot Access Control",
            explicit_ai_bot_count(context.robots.text) > 0 if context.robots.ok else False,
        ),
        ProbeResult(
            "Content Signals in robots.txt",
            "Bot Access Control",
            bool(content_signal_tokens(context.robots.text)) if context.robots.ok else False,
        ),
        ProbeResult(
            "auth.md",
            "Bot Access Control",
            auth_md_available,
            included=include_account_auth,
        ),
    ]
    probes.extend(
        ProbeResult(
            label,
            category,
            passed,
            included=_scope_included(scope, include_protocols, include_account_auth, include_ecommerce),
        )
        for (label, _, _, category, scope, _), passed in zip(_JSON_SURFACES, json_surface_results, strict=True)
    )

    included_probes = [probe for probe in probes if probe.included]
    passed = [probe.label for probe in included_probes if probe.passed]
    missing = [probe.label for probe in included_probes if not probe.passed]
    excluded = [probe.label for probe in probes if not probe.included]
    earned = len(passed)
    maximum = len(included_probes)
    score = round((earned / maximum) * 100) if maximum else 0

    if score >= 70:
        label = "Strong agent-native readiness"
    elif score >= 40:
        label = "Developing agent-native readiness"
    elif score > 0:
        label = "Limited agent-native readiness"
    else:
        label = "Limited agent-native readiness"

    return AgentReadinessSummary(
        score=score,
        earned=earned,
        max=maximum,
        label=label,
        categories=_category_summaries(probes),
        passed=passed,
        missing=missing,
        not_checked=[
            *[f"{label} excluded by audit scope." for label in excluded],
            "WebMCP runtime tool registration requires browser instrumentation and is available in advanced coverage.",
            "Verified crawler IP treatment, live search rankings, live index coverage, and model citation share require external or authenticated data.",
        ],
        benchmark=build_agent_benchmark_comparison(
            score,
            earned,
            maximum,
            include_protocols,
            include_account_auth,
            include_ecommerce,
        ),
        caveat=(
            "This strict lens follows explicit agent-native discovery signals selected for "
            "this audit scope. A site can score well on SEO basics or general crawlability "
            "while still scoring low here if it has not published relevant agent-specific surfaces."
        ),
    )
