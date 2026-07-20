from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.presets import VALID_PRESETS, PresetName, _VALID_OVERRIDE_KEYS

PillarName = Literal["off_site", "scrapability", "seo"]
EffortLevel = Literal["low", "medium", "high"]

# F3-03: a curated OpenAPI example for the audit request body. The example is
# the smallest legal payload (URL only) so agents can copy/paste it as a
# smoke test without needing to understand presets, overrides, or scope
# toggles on first contact.
_AUDIT_REQUEST_EXAMPLE: dict = {
    "url": "https://example.com/",
}

# F3-03: a second example showing the preset path so agents discover the
# QA5-03 preset model from the schema without reading docs first.
_AUDIT_REQUEST_PRESET_EXAMPLE: dict = {
    "url": "https://example.com/",
    "preset": "blog",
}


class AuditRequest(BaseModel):
    # F3-03: OpenAPI examples + field-level descriptions so the public schema
    # is self-explanatory for agents. The model_config block provides the
    # JSON Schema ``examples`` array surfaced under AuditRequest in OpenAPI.
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                _AUDIT_REQUEST_EXAMPLE,
                _AUDIT_REQUEST_PRESET_EXAMPLE,
            ],
        },
    )

    url: str = Field(
        description=(
            "Public HTTP(S) URL to audit. The scheme is added automatically if "
            "missing (defaults to https). Private, loopback, link-local, and "
            "reserved address ranges are rejected before the audit starts."
        ),
        examples=["https://example.com/"],
    )
    # F3-03: the three legacy booleans remain part of the public contract
    # (existing integrations depend on them) but the QA5-03 preset path is
    # the recommended one. Marking the fields deprecated surfaces this in
    # generated client SDKs and OpenAPI tooling without breaking callers.
    include_protocols: bool = Field(
        default=False,
        deprecated=True,
        description=(
            "Legacy scope toggle. When ``preset`` is set this field is "
            "ignored; when ``preset`` is None, this controls whether "
            "protocol/API surfaces (MCP, A2A, Agent Skills, WebMCP, API "
            "catalog) are scored. Prefer the preset-based path for new "
            "integrations; this field is preserved for backward compatibility "
            "and remains part of the public contract."
        ),
    )
    include_account_auth: bool = Field(
        default=False,
        deprecated=True,
        description=(
            "Legacy scope toggle. When ``preset`` is set this field is "
            "ignored; when ``preset`` is None, this controls whether "
            "account/auth surfaces (OAuth/OIDC discovery, auth.md) are "
            "scored. Prefer the preset-based path for new integrations; this "
            "field is preserved for backward compatibility and remains part "
            "of the public contract."
        ),
    )
    include_ecommerce: bool = Field(
        default=False,
        deprecated=True,
        description=(
            "Legacy scope toggle. When ``preset`` is set this field is "
            "ignored; when ``preset`` is None, this controls whether "
            "commerce surfaces (Product/Offer JSON-LD, price/availability, "
            "checkout-protocol metadata) are scored. Prefer the preset-based "
            "path for new integrations; this field is preserved for backward "
            "compatibility and remains part of the public contract."
        ),
    )
    preset: PresetName | None = Field(
        default=None,
        description=(
            "QA5-03 preset identifier. When set, the preset wins over the "
            "three legacy include_* booleans. When None, the booleans are "
            "honored verbatim and custom_overrides must be empty. Supported "
            "values: 'blog', 'corporate', 'services', 'ecommerce', 'news', "
            "'saas', 'custom'."
        ),
        examples=["blog", "saas", None],
    )
    custom_overrides: dict[str, bool] | None = Field(
        default=None,
        description=(
            "QA5-03 Custom/Power User overrides applied on top of the "
            "selected preset. Requires preset='custom' or another preset to "
            "be set; rejected when preset is None. Supported keys: "
            "'protocols', 'account_auth', 'ecommerce', 'feed_discovery', "
            "'article_schema', 'localbusiness_schema', 'news_article_schema', "
            "'claimreview_schema', 'product_offer_schema', 'commerce_fields', "
            "'api_catalog', 'mcp', 'a2a', 'agent_skills', 'webmcp', "
            "'oauth_oidc', 'ard_catalog', 'auth_md'."
        ),
        examples=[None, {"feed_discovery": True, "api_catalog": True}],
    )

    @field_validator("preset")
    @classmethod
    def _validate_preset(_cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value not in VALID_PRESETS:
            raise ValueError(
                f"Unknown preset {value!r}; valid presets: {sorted(VALID_PRESETS)}"
            )
        return value

    @field_validator("custom_overrides")
    @classmethod
    def _validate_override_keys(
        _cls, value: dict[str, bool] | None
    ) -> dict[str, bool] | None:
        if value is None:
            return value
        unknown = sorted(set(value) - _VALID_OVERRIDE_KEYS)
        if unknown:
            raise ValueError(
                f"Unknown custom_overrides keys: {unknown}; "
                f"valid keys: {sorted(_VALID_OVERRIDE_KEYS)}"
            )
        return value


class CheckResult(BaseModel):
    """One row of the public audit report.

    A row corresponds to a check group (for example ``robots_txt`` or
    ``schema_ld``) that may bundle several underlying sub-signals. Rows that
    are not yet scored are returned with ``state='warn'`` and
    ``evidence_level='unknown'`` rather than being omitted from the response.
    """

    pillar: PillarName = Field(
        description=(
            "Pillar this row contributes to. ``off_site`` covers off-site "
            "presence and entity metadata; ``scrapability`` covers AI "
            "crawler access, structured data, and machine-readable surfaces; "
            "``seo`` covers traditional search discovery signals."
        ),
        examples=["scrapability"],
    )
    check_name: str = Field(
        description=(
            "Stable machine identifier for the check group. Use this key "
            "rather than ``label`` for downstream filtering; ``label`` is "
            "human-readable and may change without a contract bump."
        ),
        examples=["robots_txt", "schema_ld", "llms_txt"],
    )
    label: str = Field(
        description=(
            "Human-readable title for the check group, suitable for "
            "displaying in dashboards."
        ),
        examples=["AI Bot Policy Signals"],
    )
    state: Literal["pass", "partial", "fail", "warn", "locked"] = Field(
        description=(
            "Outcome state. ``pass`` and ``partial`` mean the row scored "
            "evidence on the live site; ``fail`` means the row ran and the "
            "site did not satisfy it; ``warn`` means the row is inconclusive "
            "(check failed to complete or returned no signal); ``locked`` "
            "means the row is reserved for a paid tier and is not scored in "
            "Essentials."
        ),
        examples=["partial"],
    )
    evidence_level: Literal[
        "verified", "inferred", "unknown", "not_applicable"
    ] = Field(
        default="inferred",
        description=(
            "How strong the evidence behind this row is. ``verified`` means "
            "the live public response was inspected; ``inferred`` means the "
            "row used deterministic heuristics or metadata; ``unknown`` "
            "means the row could not be evaluated; ``not_applicable`` means "
            "the row was excluded by the active scope/preset. Default is "
            "``inferred`` (F4-17 e): most check results combine deterministic "
            "heuristics with snapshot evidence rather than a full verified "
            "live-response crawl, so the safer default avoids overclaiming "
            "verification when callers omit the field."
        ),
        examples=["inferred"],
    )
    available_in: str = Field(
        default="Essentials",
        description=(
            "Tier or surface where this row is fully evaluated. ``Essentials`` "
            "means the row is scored in the free audit; ``Starter`` and "
            "``Pro`` mean the row is locked advanced coverage and not scored "
            "in Essentials."
        ),
        examples=["Essentials", "Starter", "Pro"],
    )
    score: int = Field(
        description=(
            "Points earned on this row. Sum of all included row scores is "
            "the per-pillar score; overall score is the sum across the three "
            "pillars, capped at the rubric (100 points total)."
        ),
        examples=[3],
    )
    max_score: int = Field(
        description=(
            "Maximum points this row can earn when the active scope includes "
            "every surface the row checks. The checked denominator across all "
            "Essentials rows is 56 by default and grows when scope toggles "
            "add protocol, account/auth, or commerce surfaces."
        ),
        examples=[6],
    )
    finding: str = Field(
        description=(
            "Public-facing explanation of what was observed. Safe to surface "
            "directly in dashboards; never contains internal infrastructure "
            "detail or fetched response bodies."
        ),
        examples=["The robots.txt file does not mention the GPTBot user-agent."],
    )
    fix: str = Field(
        description=(
            "Public-facing remediation hint tied to this finding. The hint is "
            "deterministic and never presumes access to internal tooling."
        ),
        examples=[
            "Add an explicit GPTBot directive (Allow or Disallow) to robots.txt."
        ],
    )
    effort: EffortLevel = Field(
        description=(
            "Estimated effort to apply the suggested fix. ``low`` is a "
            "single-file config edit; ``medium`` is a small code or template "
            "change; ``high`` is a structural site change."
        ),
        examples=["low"],
    )


class AuditScope(BaseModel):
    include_protocols: bool = Field(
        description=(
            "Resolved protocol/API scope for this audit. ``True`` when "
            "protocol families (API catalog, MCP, A2A, Agent Skills, WebMCP) "
            "are scored; derived from the preset when one is applied."
        ),
    )
    include_account_auth: bool = Field(
        description=(
            "Resolved account/auth scope. ``True`` when OAuth/OIDC or "
            "auth.md surfaces are scored."
        ),
    )
    include_ecommerce: bool = Field(
        description=(
            "Resolved commerce scope. ``True`` when Product/Offer JSON-LD, "
            "price/availability, and commerce-protocol metadata are scored."
        ),
    )
    label: str = Field(
        description=(
            "Human-readable label for the resolved scope, suitable for "
            "displaying next to the score. For preset audits this is the "
            "preset label (for example ``'Blog/Content audit'``); for legacy "
            "boolean audits this is the legacy ``General website + extras`` "
            "string."
        ),
        examples=["Blog/Content audit"],
    )
    included_optional_surfaces: list[str] = Field(
        description=(
            "Optional surfaces (protocol, account/auth, commerce) that were "
            "enabled for this audit. Empty when only the universal core is "
            "scored."
        ),
    )
    excluded_optional_surfaces: list[str] = Field(
        description=(
            "Optional surfaces that were deliberately excluded by the "
            "preset/legacy booleans. The audit never penalises a site for "
            "excluded surfaces."
        ),
    )
    # QA5-03 preset resolution output (added fields).
    preset_applied: str | None = Field(
        default=None,
        description=(
            "QA5-03 preset identifier that won scope resolution for this "
            "audit, or ``null`` when the legacy boolean path was used."
        ),
        examples=[None, "blog", "saas"],
    )
    overrides_applied: dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "Custom/Power User override keys that the audit actually applied "
            "on top of the preset defaults. Empty for legacy audits."
        ),
    )
    included_families: list[str] = Field(
        default_factory=list,
        description=(
            "Sub-signal families that were enabled for this audit after "
            "preset + override resolution. Universal-core families are not "
            "listed here."
        ),
    )
    excluded_families: list[str] = Field(
        default_factory=list,
        description=(
            "Sub-signal families that were explicitly disabled for this "
            "audit (by preset, override, or universal exclusion)."
        ),
    )
    machine_surfaces_scope: Literal["common-contextual", "full"] = Field(
        default="common-contextual",
        description=(
            "Agent Protocol Discovery depth. ``common-contextual`` is the "
            "default bounded probe; ``full`` is selected by presets that "
            "explicitly opt into the complete protocol surface set."
        ),
        examples=["common-contextual", "full"],
    )


class PillarScores(BaseModel):
    off_site: int = Field(
        description="Points earned on the off-site pillar (0-30).",
    )
    scrapability: int = Field(
        description="Points earned on the AI access / scrapability pillar (0-40).",
    )
    seo: int = Field(
        description="Points earned on the SEO pillar (0-30).",
    )


class PillarMax(BaseModel):
    off_site: int = Field(
        description="Maximum off-site pillar score possible under the resolved scope.",
    )
    scrapability: int = Field(
        description="Maximum AI access pillar score possible under the resolved scope.",
    )
    seo: int = Field(
        description="Maximum SEO pillar score possible under the resolved scope.",
    )


class BenchmarkEntry(BaseModel):
    name: str = Field(description="Display name for the benchmark peer.")
    category: str = Field(
        description=(
            "Peer category bucket used by the benchmark profile generator "
            "(for example, ``'blog'``, ``'saas'``, ``'ecommerce'``)."
        ),
    )
    group: str = Field(
        description=(
            "Peer size/cohort group (``'small'``, ``'medium'``, ``'large'``) "
            "used for percentile grouping."
        ),
    )
    size: str = Field(
        description="Human-readable size label for the peer.",
    )
    url: str = Field(
        description=(
            "Public benchmark peer URL. Safe to surface in dashboards; no "
            "private benchmarks are exposed through this field."
        ),
    )
    overall_score: int = Field(description="Peer overall score at snapshot time.")
    free_evidence_score: int = Field(
        description="Peer free-evidence score at snapshot time."
    )
    checked_score: int = Field(description="Peer checked-score at snapshot time.")
    checked_max: int = Field(description="Peer checked denominator at snapshot time.")
    agent_readiness_score: int = Field(
        description="Peer strict agent-readiness score at snapshot time."
    )
    agent_readiness_earned: int = Field(
        description="Peer strict agent-readiness earned points."
    )
    agent_readiness_max: int = Field(
        description="Peer strict agent-readiness denominator."
    )
    pillar_scores: PillarScores = Field(
        description="Peer pillar scores at snapshot time."
    )


class BenchmarkComparison(BaseModel):
    score: int = Field(
        description=(
            "The site's free-evidence score (Essentials evidence only, no "
            "locked rows). This is the score used for peer comparison."
        ),
    )
    checked_score: int = Field(
        description="Checked score earned by the site on this audit."
    )
    checked_max: int = Field(
        description="Checked denominator for the resolved scope of this audit."
    )
    benchmark_count: int = Field(
        description="Number of public benchmark peers considered."
    )
    median_score: int = Field(
        description="Median free-evidence score across public benchmark peers."
    )
    percentile: int = Field(
        description=(
            "Site's percentile rank against public peers (0-100). 100 means "
            "the site scored at or above every peer; 0 means it scored at or "
            "below every peer."
        ),
    )
    position_label: str = Field(
        description=(
            "Human-readable position label (for example, ``'Above median'`` "
            "or ``'Below median'``). Always paired with the numeric "
            "percentile; never the only signal."
        ),
        examples=["Above median", "At median", "Below median"],
    )
    nearest: list[BenchmarkEntry] = Field(
        description=(
            "Closest peers by free-evidence score, used for explainable "
            "comparison. Empty when no peers are available."
        ),
    )
    entries: list[BenchmarkEntry] = Field(
        description=(
            "All public peers considered for this comparison. Empty when no "
            "public profiles are loaded; private production profiles are "
            "never exposed through this field."
        ),
    )
    basis: str = Field(
        description=(
            "Plain-text description of how the benchmark was computed "
            "(profile source, denominator, scope filter). Always includes "
            "the public/private source distinction."
        ),
    )
    snapshot_date: str = Field(
        description=(
            "ISO date the benchmark snapshot was generated. Benchmark "
            "profiles are refreshed when scoring, options, or applicability "
            "rules change."
        ),
        examples=["2026-07-17"],
    )
    caveat: str = Field(
        description=(
            "Mandatory caveat explaining what the benchmark does and does "
            "not prove. Agents should surface this text alongside any "
            "percentile or comparison claim."
        ),
    )


class AgentBenchmarkComparison(BaseModel):
    score: int = Field(description="Site's strict agent-readiness score.")
    earned: int = Field(description="Points earned on the strict agent-readiness lens.")
    max: int = Field(
        description="Strict agent-readiness denominator for the resolved scope."
    )
    benchmark_count: int = Field(
        description="Number of public peers considered for the agent benchmark."
    )
    median_score: int = Field(
        description="Median strict agent-readiness score across public peers."
    )
    percentile: int = Field(
        description="Site's percentile rank on the strict agent-readiness lens."
    )
    position_label: str = Field(
        description="Human-readable agent-benchmark position label."
    )
    nearest: list[BenchmarkEntry] = Field(
        description="Closest peers by strict agent-readiness score."
    )
    entries: list[BenchmarkEntry] = Field(
        description="All public peers considered for the agent benchmark."
    )
    basis: str = Field(
        description="Plain-text basis string for the agent benchmark."
    )
    snapshot_date: str = Field(
        description="ISO date the agent-benchmark snapshot was generated."
    )
    caveat: str = Field(
        description="Mandatory caveat explaining what the agent benchmark does and does not prove."
    )


class AgentReadinessCategory(BaseModel):
    name: str = Field(description="Category name (for example, ``'Discovery'``).")
    earned: int = Field(description="Points earned in this category.")
    max: int = Field(description="Maximum points for this category.")
    score: int = Field(description="Category percentage score (0-100).")
    passed: list[str] = Field(
        description="Public signals that this category verified on the site."
    )
    missing: list[str] = Field(
        description="Public signals this category looked for and did not find."
    )
    excluded: list[str] = Field(
        description=(
            "Surfaces excluded by the active scope/preset. Listed for "
            "transparency; they do not affect the category score."
        ),
    )


class AgentReadinessSummary(BaseModel):
    score: int = Field(
        description=(
            "Strict agent-readiness percentage score (0-100). Measures "
            "explicit agent-native discovery and protocol signals; stricter "
            "than general crawlability or SEO."
        ),
    )
    earned: int = Field(
        description="Points earned on the strict agent-readiness lens."
    )
    max: int = Field(
        description="Strict agent-readiness denominator for the resolved scope."
    )
    label: str = Field(
        description="Human-readable label for the strict agent-readiness score.",
        examples=[
            "Strong agent-native readiness",
            "Developing agent-native readiness",
            "Limited agent-native readiness",
            "Agent-native unavailable",
        ],
    )
    categories: list[AgentReadinessCategory] = Field(
        description="Per-category breakdown of the strict agent-readiness score."
    )
    passed: list[str] = Field(
        description="All agent-native signals verified on the site."
    )
    missing: list[str] = Field(
        description="All agent-native signals the audit looked for and did not find."
    )
    not_checked: list[str] = Field(
        description=(
            "Agent-native surfaces the audit deliberately did not check, "
            "for example when an individual probe failed and produced an "
            "inconclusive warning instead of verified evidence."
        ),
    )
    benchmark: AgentBenchmarkComparison = Field(
        description="Peer comparison on the strict agent-readiness lens."
    )
    caveat: str = Field(
        description=(
            "Mandatory caveat explaining what the strict agent-readiness "
            "score does and does not prove."
        ),
    )


class AuditResult(BaseModel):
    # F3-02: API versioning. Additive only — the field carries the contract
    # version that produced this response. Clients may rely on it to detect
    # future additions without breaking on them.
    api_version: str = Field(
        default="1.0",
        description=(
            "MachineRead API contract version that produced this response. "
            "Add-only: the field value is bumped when the contract changes "
            "in a breaking way. Agents should treat unknown api_version "
            "values as forward-compatible and ignore unknown fields rather "
            "than reject the response."
        ),
        examples=["1.0"],
    )
    url: str = Field(
        description=(
            "Final audited URL after normalization. Always starts with "
            "``https://`` (or ``http://`` when the user explicitly opted "
            "into plain HTTP) and ends with a trailing slash."
        ),
    )
    scope: AuditScope = Field(
        description="Resolved audit scope (see AuditScope).",
    )
    overall_score: int = Field(
        description=(
            "Full-rubric score on a 100-point scale. Includes locked "
            "advanced rows (score 0 until verified) and capped at the "
            "active pillar maxima."
        ),
    )
    pillar_scores: PillarScores = Field(
        description="Per-pillar earned points."
    )
    pillar_max: PillarMax = Field(
        description="Per-pillar maximum under the resolved scope."
    )
    agent_readiness: AgentReadinessSummary = Field(
        description=(
            "Strict agent-native readiness summary. Stricter than the "
            "general scrapability pillar; measures explicit agent-protocol "
            "discovery and signal verification."
        ),
    )
    benchmark: BenchmarkComparison = Field(
        description=(
            "Public peer comparison on the Essentials evidence score. "
            "Private production benchmark profiles are never exposed; only "
            "public-safe snapshot entries are returned."
        ),
    )
    checks: list[CheckResult] = Field(
        description=(
            "Per-check group results. Length is 13 for a default Essentials "
            "audit; locked advanced rows are appended at the bottom with "
            "``state='locked'``."
        ),
    )


# F3-02: explicit public error models. These shapes are referenced in the
# `responses={...}` mapping on `/v1/audit` so the autogenerated OpenAPI schema
# documents the exact contract for agents.
class ErrorMessage(BaseModel):
    detail: str = Field(
        description="Human-readable explanation of why the request was rejected.",
        examples=[
            "URL resolves to a private address. Retry with a public HTTP(S) endpoint."
        ],
    )


class ValidationErrorMessage(BaseModel):
    detail: str = Field(
        description="Human-readable explanation of the validation failure.",
        examples=[
            "Request body failed validation: url is required.",
        ],
    )
    errors: list[dict] = Field(
        default_factory=list,
        description=(
            "Structured per-field or per-rule error descriptors. FastAPI's "
            "default 422 response uses a list of `{loc, msg, type}` items; "
            "preset override failures expose the offending keys and reasons."
        ),
        examples=[[]],
    )


class RateLimitErrorMessage(BaseModel):
    detail: str = Field(
        description="Human-readable explanation that the request was rate-limited.",
        examples=[
            "Rate limit exceeded. Retry after the time indicated by Retry-After.",
        ],
    )
    retry_after: int = Field(
        description=(
            "Whole seconds the client should wait before retrying. Mirrors "
            "the `Retry-After` response header and the `X-RateLimit-Reset` "
            "value."
        ),
        examples=[42],
    )


# F3-12: agent-oriented compact report summary. ``AuditSummary`` is the
# strict-mode projection of ``AuditResult`` returned by
# ``POST /v1/audit/summary``. It excludes LLM prose, finding/fix strings,
# benchmark peer entries, agent-readiness passed/missing/not_checked lists,
# rate-limit body metadata, and raw context.
_SUMMARY_CHECK_NAME = Literal[
    "robots_txt",
    "bot_access",
    "html_structure",
    "schema_ld",
    "llms_txt",
    "ssr",
    "machine_surfaces",
    "pagespeed",
    "canonical",
    "indexing",
    "search_discovery",
    "social",
    "wikipedia",
]
_SUMMARY_PILLAR = Literal["off_site", "scrapability", "seo"]
_SUMMARY_STATE = Literal["fail", "partial", "warn"]
_SUMMARY_EVIDENCE = Literal["verified", "inferred", "sampled", "unavailable"]
_SUMMARY_EFFORT = Literal["low", "medium", "high"]


class SummaryScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    preset: Literal["blog", "corporate", "services", "ecommerce", "news", "saas", "custom"] | None = Field(
        description=(
            "Preset identifier that won scope resolution for this audit, or "
            "``null`` when the legacy boolean path was used."
        ),
        examples=[None, "blog", "saas"],
    )
    protocols: bool = Field(
        description=(
            "Resolved protocol/API scope for the audit. ``True`` when the "
            "active scope scores protocol-family surfaces."
        ),
    )
    account_auth: bool = Field(
        description=(
            "Resolved account/auth scope for the audit. ``True`` when the "
            "active scope scores account/auth surfaces."
        ),
    )
    ecommerce: bool = Field(
        description=(
            "Resolved commerce scope for the audit. ``True`` when the active "
            "scope scores commerce surfaces."
        ),
    )
    overrides: dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "Custom/Power User override keys the audit applied on top of the "
            "preset defaults. Empty when the legacy boolean path was used."
        ),
    )


class SummaryScorePair(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    earned: int = Field(description="Points earned on the associated scope.")
    max: int = Field(description="Maximum points possible on the associated scope.")


class SummaryPercent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    percent: int = Field(description="Percentage score for the associated scope (0-100).")
    earned: int = Field(description="Points earned on the associated scope.")
    max: int = Field(description="Maximum points possible on the associated scope.")


class SummaryScores(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    overall: SummaryScorePair = Field(
        description=(
            "Full-rubric overall score. ``max`` is the sum of pillar caps "
            "(100) regardless of the active scope."
        ),
    )
    pillars: dict[str, SummaryScorePair] = Field(
        description=(
            "Per-pillar earned and max points. Keys are always "
            "``off_site``, ``scrapability``, and ``seo``."
        ),
    )
    essentials: SummaryPercent = Field(
        description=(
            "Essentials evidence score (peer comparison source). ``percent`` "
            "mirrors ``AuditResult.benchmark.score``; ``earned`` and ``max`` "
            "are the source ``checked_score`` and ``checked_max``."
        ),
    )
    agent_readiness: SummaryPercent = Field(
        description=(
            "Strict agent-readiness score. ``percent`` mirrors "
            "``AuditResult.agent_readiness.score``; ``earned`` and ``max`` "
            "are the source ``earned`` and ``max``."
        ),
    )


class SummaryBenchmark(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    percentile: int = Field(description="Site percentile rank against public peers (0-100).")
    median_percent: int = Field(
        description=(
            "Median peer score (percent) the benchmark comparison used. The "
            "source field ``BenchmarkComparison.median_score`` is renamed to "
            "``median_percent`` to make the unit explicit in the agent surface."
        ),
    )
    peer_count: int = Field(description="Number of public benchmark peers considered.")
    snapshot: str = Field(
        description=(
            "ISO date the benchmark snapshot was generated. Benchmark profiles "
            "are refreshed when scoring, options, or applicability rules change."
        ),
    )


class SummaryCheckCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    included: int = Field(description="Count of non-locked Essentials check rows (13).")
    locked: int = Field(description="Count of locked advanced rows (9).")
    # ``pass`` is a reserved keyword in Python. Pydantic v2 allows aliases
    # but we want the public JSON key to remain ``pass`` (matches the
    # runtime terminology). ``populate_by_name`` lets the constructor use
    # ``pass_count`` while the serialized JSON key stays ``pass``.
    pass_count: int = Field(
        alias="pass",
        description="Number of included rows with state ``pass``.",
    )
    partial: int = Field(description="Number of included rows with state ``partial``.")
    fail: int = Field(description="Number of included rows with state ``fail``.")
    warn: int = Field(description="Number of included rows with state ``warn``.")
    attention_total: int = Field(
        description=(
            "Count of items in the ``attention`` array after the 5-row cap. "
            "This matches the actual returned count rather than the pre-cap "
            "candidate count so the field never disagrees with the array length."
        ),
    )


class SummaryAttentionItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    check_name: _SUMMARY_CHECK_NAME = Field(
        description="Stable machine identifier for the check group.",
    )
    pillar: _SUMMARY_PILLAR = Field(description="Pillar the row contributes to.")
    state: _SUMMARY_STATE = Field(description="Attention state of the row.")
    evidence_level: _SUMMARY_EVIDENCE = Field(
        description="Evidence maturity for the row.",
    )
    earned: int = Field(description="Points earned on the row.")
    max: int = Field(description="Maximum points possible on the row.")
    effort: _SUMMARY_EFFORT = Field(description="Estimated remediation effort.")


# F3-12: fixed ordered tuple of limitation codes that always appear in the
# 200 payload in the same order. Exposed as a module-level constant so
# tests, projection code, and the OpenAPI examples all reference the same
# source of truth.
SUMMARY_LIMITATION_CODES: tuple[
    Literal["relative_scores"],
    Literal["no_live_ranking"],
    Literal["no_provider_ip_auth"],
    Literal["no_paid_crawlers"],
] = (
    "relative_scores",
    "no_live_ranking",
    "no_provider_ip_auth",
    "no_paid_crawlers",
)


class AuditSummary(BaseModel):
    # F3-12: the agent-oriented compact report summary. The shape is
    # strict, additive-only, and intentionally smaller than
    # ``AuditResult`` so agents can reason about Essentials without
    # pulling the full per-check evidence into context.
    model_config = ConfigDict(extra="forbid", strict=True)

    api_version: Literal["1.0"] = Field(
        description=(
            "MachineRead API contract version that produced this response. "
            "Same semantics as ``AuditResult.api_version``."
        ),
    )
    summary_version: Literal["1.0"] = Field(
        description=(
            "Summary contract version. Bumped when ``AuditSummary`` adds or "
            "renames a field in a breaking way."
        ),
    )
    url: str = Field(description="Final audited URL after normalization.")
    scope: SummaryScope = Field(description="Resolved audit scope (compact form).")
    scores: SummaryScores = Field(description="Compact scoring surface.")
    benchmarks: dict[str, SummaryBenchmark] = Field(
        description=(
            "Compact benchmark comparison. Always carries the keys "
            "``essentials`` and ``agent_readiness``."
        ),
    )
    checks: SummaryCheckCounts = Field(description="Compact check row counts.")
    attention: list[SummaryAttentionItem] = Field(
        description=(
            "Up to 5 most-urgent attention rows, sorted by severity then "
            "ratio then check name. Empty when every included row passes."
        ),
    )
    limitations: tuple[str, str, str, str] = Field(
        description=(
            "Fixed ordered tuple of limitation codes that always appear in "
            "the 200 payload: ``relative_scores``, ``no_live_ranking``, "
            "``no_provider_ip_auth``, ``no_paid_crawlers``."
        ),
    )