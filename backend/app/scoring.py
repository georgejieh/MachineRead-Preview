from app.benchmarks import build_benchmark_comparison
from app.models import AgentReadinessSummary, AuditResult, AuditScope, CheckResult, PillarMax, PillarScores
from app.checks.locked import locked_checks
from app.presets import ResolvedScope, _resolve_legacy

_PILLAR_MAX = PillarMax(off_site=30, scrapability=40, seo=30)

# F3-02: keep the API version constant in one place so the same value flows
# through scoring, models, and OpenAPI metadata. Bumping this is the contract
# signal used by agents to detect additive changes vs. breaking ones.
API_VERSION = "1.0"

_PILLAR_CHECKS: dict[str, list[str]] = {
    "off_site": [
        "social",
        "wikipedia",
        "earned_mentions_backlinks",
        "owned_social_presence",
        "social_traction_reviews",
        "ai_citation_share",
    ],
    "scrapability": [
        "robots_txt",
        "bot_access",
        "html_structure",
        "schema_ld",
        "llms_txt",
        "ssr",
        "machine_surfaces",
        "extraction_fidelity",
        "agent_task_simulation",
    ],
    "seo": [
        "pagespeed",
        "canonical",
        "indexing",
        "search_discovery",
        "multi_engine_index_coverage",
        "core_web_vitals",
        "keyword_competitor_gap",
    ],
}


def _pillar_score(checks: list[CheckResult], pillar: str, cap: int) -> int:
    total = sum(c.score for c in checks if c.pillar == pillar)
    return min(total, cap)


def _scope_label(scope: ResolvedScope) -> str:
    """Compose the user-facing scope label.

    Preset wins: when a preset is applied, use its display label verbatim.
    Otherwise, fall back to the legacy "General website + extras" string so
    older callers keep seeing familiar copy.
    """

    if scope.preset:
        return scope.preset_label

    legacy = _resolve_legacy(
        scope.include_protocols,
        scope.include_account_auth,
        scope.include_ecommerce,
    )
    return legacy.preset_label


def build_result(
    url: str,
    checks: list[CheckResult],
    agent_readiness: AgentReadinessSummary,
    scope: ResolvedScope | None = None,
    include_protocols: bool = False,
    include_account_auth: bool = False,
    include_ecommerce: bool = False,
) -> AuditResult:
    """Assemble the final AuditResult from individual check results.

    The ``scope`` argument is the QA5-03 preferred path. The three legacy
    booleans remain supported for backwards-compatible callers (and are what
    the existing tests use); when ``scope`` is None, the legacy booleans are
    promoted into a synthesised :class:`ResolvedScope` so the rest of the
    pipeline only has one source of truth.
    """

    if scope is None:
        scope = _resolve_legacy(
            include_protocols, include_account_auth, include_ecommerce
        )

    checks = checks + locked_checks(scope.include_ecommerce)
    included_optional_surfaces = [
        "Core discoverability",
        "Content accessibility",
        "Bot access control",
        "Structured data",
        "Search discovery hints",
    ]
    excluded_optional_surfaces = []
    if scope.include_protocols:
        included_optional_surfaces.append("API and protocol discovery")
    else:
        excluded_optional_surfaces.append("API and protocol discovery")
    if scope.include_account_auth:
        included_optional_surfaces.append("Account/auth discovery")
    else:
        excluded_optional_surfaces.append("Account/auth discovery")
    if scope.include_ecommerce:
        included_optional_surfaces.extend(["Commerce protocol metadata", "Catalog JSON"])
    else:
        excluded_optional_surfaces.extend(["Commerce protocol metadata", "Catalog JSON"])

    audit_scope = AuditScope(
        include_protocols=scope.include_protocols,
        include_account_auth=scope.include_account_auth,
        include_ecommerce=scope.include_ecommerce,
        label=_scope_label(scope),
        included_optional_surfaces=included_optional_surfaces,
        excluded_optional_surfaces=excluded_optional_surfaces,
        preset_applied=scope.preset,
        overrides_applied=dict(scope.overrides),
        included_families=list(scope.included_families),
        excluded_families=list(scope.excluded_families),
        machine_surfaces_scope=scope.machine_surfaces,
    )
    pillar_scores = PillarScores(
        off_site=_pillar_score(checks, "off_site", _PILLAR_MAX.off_site),
        scrapability=_pillar_score(checks, "scrapability", _PILLAR_MAX.scrapability),
        seo=_pillar_score(checks, "seo", _PILLAR_MAX.seo),
    )
    overall = pillar_scores.off_site + pillar_scores.scrapability + pillar_scores.seo

    sorted_checks = sorted(
        checks,
        key=lambda c: (c.max_score - c.score) / max({"low": 1, "medium": 2, "high": 3}.get(c.effort, 2), 1),
        reverse=True,
    )

    return AuditResult(
        api_version=API_VERSION,
        url=url,
        scope=audit_scope,
        overall_score=overall,
        pillar_scores=pillar_scores,
        pillar_max=_PILLAR_MAX,
        agent_readiness=agent_readiness,
        benchmark=build_benchmark_comparison(
            checks,
            scope.include_protocols,
            scope.include_account_auth,
            scope.include_ecommerce,
        ),
        checks=sorted_checks,
    )