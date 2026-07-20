import asyncio
import json
import logging
import os
from collections.abc import Awaitable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import Response

from app.agent_readiness import build_agent_readiness_summary, _scope_probe_labels
from app.audit_context import build_audit_context
from app.benchmarks import build_agent_benchmark_comparison
from app.checks.bot_access import check_bot_access
from app.checks.canonical import check_canonical
from app.checks.html_structure import check_html_structure
from app.checks.indexing import check_indexing
from app.checks.llms_txt import check_llms_txt
from app.checks.machine_surfaces import check_machine_surfaces
from app.checks.pagespeed import check_pagespeed
from app.checks.robots import check_robots
from app.checks.schema_ld import check_schema_ld
from app.checks.search_discovery import check_search_discovery
from app.checks.social import check_social
from app.checks.ssr import check_ssr
from app.checks.wikipedia import check_wikipedia
from app.essential_runner import (
    _agent_readiness_max,
    _fallback_agent_readiness_summary,
    _fallback_check_result,
    _safe_agent_readiness_summary,
    _safe_check,
)
from app.models import (
    AgentReadinessSummary,
    AuditRequest,
    AuditResult,
    AuditSummary,
    CheckResult,
    ErrorMessage,
    RateLimitErrorMessage,
    ValidationErrorMessage,
)
from app.presets import resolve_scope, validate_overrides
from app.qa2_evidence import collect_qa2_evidence
from app.rubric import ESSENTIALS_CHECK_GROUPS, EssentialsCheckGroup
from app.report_summary import build_report_summary
from app.scoring import API_VERSION, build_result
from app.ssrf import validate_url

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_AUDIT_RATE_LIMIT = "3/minute"
DEFAULT_LOCAL_FRONTEND_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "http://localhost:3002",
    "http://127.0.0.1:3002",
]


def audit_rate_limit() -> str:
    configured_limit = os.getenv("MACHINEREAD_AUDIT_RATE_LIMIT", "").strip()
    return configured_limit or DEFAULT_AUDIT_RATE_LIMIT


def _configured_cors_origins() -> list[str]:
    configured_origins = os.getenv("MACHINEREAD_CORS_ORIGINS", "")
    origins: list[str] = []
    for origin in configured_origins.split(","):
        stripped_origin = origin.strip()
        if stripped_origin and stripped_origin not in origins:
            origins.append(stripped_origin)
    return origins


def cors_allowed_origins() -> list[str]:
    configured_origins = _configured_cors_origins()
    environment = os.getenv("ENVIRONMENT", "").strip().lower()
    if environment == "production":
        return configured_origins

    origins = list(DEFAULT_LOCAL_FRONTEND_ORIGINS)
    for origin in configured_origins:
        if origin not in origins:
            origins.append(origin)
    return origins


limiter = Limiter(key_func=get_remote_address, headers_enabled=True)


# curated OpenAPI metadata. tags_metadata groups operations in the
# schema UI so agents can browse by surface instead of by URL. The descriptions
# are short, factual, and contain no private/internals (no benchmark profile
# filenames, no env-var names, no infra hints).
OPENAPI_TAGS: list[dict] = [
    {
        "name": "Audit",
        "description": (
            "Run the free MachineRead Essentials audit against a public "
            "HTTP(S) URL and retrieve the public contract (overall score, "
            "pillar scores, benchmark comparison, strict agent-readiness "
            "summary, and per-check group evidence). Rate-limited per client "
            "IP; the rate-limit guidance is returned on every response via "
            "the X-RateLimit-* headers and Retry-After (on 429)."
        ),
    },
    {
        "name": "System",
        "description": (
            "Operational endpoints used by uptime monitors and orchestration "
            "tooling. Does not require authentication and does not surface "
            "customer data."
        ),
    },
]

# external documentation pointer surfaced in the OpenAPI top-level
# ``externalDocs`` field. The docs URL is the public landing page; no private
# URLs or admin paths leak through the schema.
_OPENAPI_EXTERNAL_DOCS: dict = {
    "description": "MachineRead product docs, methodology, and caveats",
    "url": "https://machineread.ai/docs",
}

# list of public server URLs surfaced under ``servers``. Local is
# listed first so dev agents hit the local instance by default. No staging
# or private/internal URLs are exposed.
_OPENAPI_SERVERS: list[dict] = [
    {"url": "http://127.0.0.1:8000", "description": "Local development"},
    {"url": "https://api.machineread.ai", "description": "Public production"},
]


app = FastAPI(
    title="MachineRead API",
    version=API_VERSION,
    description=(
        "Public audit API for the free MachineRead Essentials scan. "
        "Use `POST /v1/audit` for new integrations; the unversioned `POST /audit` "
        "remains available as a compatibility alias. All responses carry an "
        "`api_version` field. Rate-limit guidance is returned on every response "
        "via `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, "
        "and `Retry-After` (on 429)."
    ),
    contact={"name": "MachineRead"},
    license_info={"name": "MIT"},
    openapi_tags=OPENAPI_TAGS,
    servers=_OPENAPI_SERVERS,
)
app.state.limiter = limiter


def _custom_openapi() -> dict:
    """Attach the curated OpenAPI ``externalDocs`` pointer to the
    autogenerated schema.

    FastAPI does not expose ``externalDocs`` as a constructor argument, so
    the schema is generated via :py:meth:`FastAPI.openapi` and augmented with
    the public docs link before being cached on the application. Only public
    URL values land in the schema — no private/admin/infra paths.
    """

    if app.openapi_schema:
        return app.openapi_schema
    schema = FastAPI.openapi(app)
    schema.setdefault("externalDocs", _OPENAPI_EXTERNAL_DOCS)
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi  # type: ignore[assignment]


def _rate_limit_response_payload(retry_after: int) -> dict:
    """Shape the SlowAPI 429 body to match ``RateLimitErrorMessage``."""

    return RateLimitErrorMessage(
        detail="Rate limit exceeded. Retry after the time indicated by Retry-After.",
        retry_after=retry_after,
    ).model_dump()


def _custom_rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """SlowAPI exception handler that emits ``RateLimitErrorMessage`` and the
    standard X-RateLimit-* headers documented in ``/v1/audit``."""

    response = JSONResponse(
        _rate_limit_response_payload(retry_after=0),
        status_code=429,
    )
    view_rate_limit = getattr(request.state, "view_rate_limit", None)
    if view_rate_limit is not None:
        response = request.app.state.limiter._inject_headers(response, view_rate_limit)
        # Surface the same retry-after value inside the JSON body so clients
        # do not have to read the header. The SlowAPI header injection above
        # already set ``Retry-After`` on the response.
        retry_after_header = response.headers.get("Retry-After")
        if retry_after_header is not None:
            try:
                response_body = _rate_limit_response_payload(int(retry_after_header))
            except ValueError:
                response_body = _rate_limit_response_payload(retry_after=0)
            response = JSONResponse(response_body, status_code=429)
            if view_rate_limit is not None:
                response = request.app.state.limiter._inject_headers(
                    response, view_rate_limit
                )
    return response


app.add_exception_handler(RateLimitExceeded, _custom_rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allowed_origins(),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

_ESSENTIAL_GROUPS_BY_NAME = {group.check_name: group for group in ESSENTIALS_CHECK_GROUPS}


def _essential_group(check_name: str) -> EssentialsCheckGroup:
    return _ESSENTIAL_GROUPS_BY_NAME[check_name]


async def _build_context_or_error(url: str):
    try:
        return await build_audit_context(url)
    except Exception as exc:
        logger.exception("audit setup failed: %s", url)
        raise HTTPException(
            status_code=500,
            detail=(
                "MachineRead could not start the audit for this URL. Retry the scan, "
                "and check server logs if the setup error repeats."
            ),
        ) from exc


async def _run_essential_checks(context, body: AuditRequest) -> list[CheckResult]:
    try:
        qa2_evidence = await collect_qa2_evidence(context, body.include_ecommerce)
    except Exception:
        logger.exception("QA2 evidence collection failed: %s", context.url)
        qa2_evidence = None
    checks = await asyncio.gather(
        _safe_check(_essential_group("robots_txt"), check_robots(context), logger=logger),
        _safe_check(_essential_group("bot_access"), check_bot_access(context), logger=logger),
        _safe_check(_essential_group("html_structure"), check_html_structure(context), logger=logger),
        _safe_check(
            _essential_group("schema_ld"),
            check_schema_ld(context, body.include_ecommerce, qa2_evidence),
            logger=logger,
        ),
        _safe_check(_essential_group("llms_txt"), check_llms_txt(context, qa2_evidence), logger=logger),
        _safe_check(_essential_group("ssr"), check_ssr(context, qa2_evidence), logger=logger),
        _safe_check(
            _essential_group("machine_surfaces"),
            check_machine_surfaces(
                context,
                body.include_protocols,
                body.include_account_auth,
                body.include_ecommerce,
            ),
            logger=logger,
        ),
        _safe_check(_essential_group("pagespeed"), check_pagespeed(context), logger=logger),
        _safe_check(_essential_group("canonical"), check_canonical(context), logger=logger),
        _safe_check(_essential_group("indexing"), check_indexing(context), logger=logger),
        _safe_check(
            _essential_group("search_discovery"),
            check_search_discovery(context, body.include_ecommerce, qa2_evidence),
            logger=logger,
        ),
        _safe_check(_essential_group("social"), check_social(context), logger=logger),
        _safe_check(_essential_group("wikipedia"), check_wikipedia(context), logger=logger),
    )
    return list(checks)


def _raise_validation_http_error(message: str, errors: list[dict] | None = None) -> None:
    """Raise a 422 ``HTTPException`` whose body matches ``ValidationErrorMessage``.

    The structured ``errors`` list is what the OpenAPI ``responses`` mapping
    advertises for 422; passing an empty list keeps the legacy ``detail``-only
    shape while still staying on the same response model.
    """

    payload = ValidationErrorMessage(detail=message, errors=list(errors or []))
    raise HTTPException(status_code=422, detail=payload.model_dump())


def _raise_url_http_error(message: str) -> None:
    """Map an SSRF ``validate_url`` error to 400 (blocked target) or 422
    (malformed URL syntax) and raise the matching ``HTTPException``."""

    if message.startswith("URL resolves to a private address"):
        raise HTTPException(status_code=400, detail=ErrorMessage(detail=message).model_dump())
    raise HTTPException(status_code=422, detail=ValidationErrorMessage(detail=message).model_dump())


@app.get(
    "/health",
    tags=["System"],
    summary="Health probe",
    description=(
        "Returns ``{\"status\": \"ok\"}`` when the API is responsive. Used by "
        "uptime monitors and orchestration tooling; safe to call without "
        "authentication."
    ),
)
async def health(request: Request, response: Response) -> dict[str, str]:
    # static discovery Link headers so uptime monitors and agents can
    # discover the public API catalog, ARD ai-catalog, OpenAPI schema,
    # llms.txt, and product docs without prior knowledge of the API surface.
    # Values are path-relative and resolve under whatever base URL the
    # reverse proxy exposes, so this works for both the local dev server
    # and the public production host.
    _BASE_HREF = str(request.base_url).rstrip("/")
    response.headers["Link"] = (
        f'</.well-known/api-catalog>; rel="api-catalog", '
        f'</openapi.json>; rel="service-desc"; '
        f'type="application/vnd.oai.openapi+json;version=3.1", '
        f'</llms.txt>; rel="llms-txt", '
        f'</docs/api/>; rel="service-doc"'
    )
    response.headers["X-API-Catalog"] = f"{_BASE_HREF}/.well-known/api-catalog"
    response.headers["X-AI-Catalog"] = f"{_BASE_HREF}/ai-catalog.json"
    return {"status": "ok"}


# serve the public API catalog linkset at the conventional
# ``/.well-known/api-catalog`` location. The source artifact lives at
# ``docs/api/api-catalog.json`` so the same content is shipped through the
# public repo export. The route loads the file once at app startup; the
# response uses the IANA-registered ``application/linkset+json`` media type
# per RFC 9264.
_API_CATALOG_SOURCE_PATH = Path(__file__).resolve().parents[2] / "docs" / "api" / "api-catalog.json"


def _load_api_catalog_linkset() -> dict:
    """Load the static API catalog linkset JSON shipped with the repo.

    The file is loaded once and cached on the app state. If the source file
    is missing in a deployed environment, the route returns a minimal empty
    linkset rather than 500-ing, so uptime monitors still get a 200.
    """

    if getattr(app.state, "api_catalog_linkset", None) is not None:
        return app.state.api_catalog_linkset
    try:
        with _API_CATALOG_SOURCE_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        payload = {"linkset": []}
    app.state.api_catalog_linkset = payload
    return payload


@app.get(
    "/.well-known/api-catalog",
    tags=["System"],
    summary="Public API catalog linkset",
    response_class=JSONResponse,
    responses={
        200: {
            "description": "RFC 9264 linkset pointing at the public OpenAPI schema, ARD ai-catalog, llms.txt, and product docs.",
            "content": {"application/linkset+json": {}},
        }
    },
)
async def api_catalog() -> JSONResponse:
    # serve the static linkset with the RFC 9264 media type so
    # generic linkset clients negotiate it without parsing the path. The
    # ``application/json`` alias is included for clients that do not know
    # the linkset media type yet.
    payload = _load_api_catalog_linkset()
    return JSONResponse(
        content=payload,
        media_type="application/linkset+json",
        headers={"Content-Type": "application/linkset+json"},
    )


# explicit error mapping so the autogenerated OpenAPI schema documents
# the exact public error shapes for agents.
# each entry now also carries an ``examples`` payload so agents can
# copy/paste a working response shape from the schema. Examples use the same
# public model classes the runtime emits, so they stay consistent with the
# generated JSON. No private/infra paths or secrets appear in the examples.
def _audit_200_example() -> dict:
    """Compose a minimal-but-realistic AuditResult example for the OpenAPI
    200 response. Only public model classes are used."""

    from app.models import (
        AgentBenchmarkComparison,
        AgentReadinessCategory,
        AgentReadinessSummary,
        AuditResult,
        BenchmarkComparison,
        BenchmarkEntry,
        CheckResult,
        PillarMax,
        PillarScores,
    )

    sample_check = CheckResult(
        pillar="scrapability",
        check_name="robots_txt",
        label="AI Bot Policy Signals",
        state="partial",
        evidence_level="verified",
        available_in="Essentials",
        score=4,
        max_score=6,
        finding="robots.txt mentions GPTBot but does not mention ClaudeBot.",
        fix="Add an explicit ClaudeBot directive (Allow or Disallow) to robots.txt.",
        effort="low",
    )
    sample_entry = BenchmarkEntry(
        name="Sample public peer",
        category="blog",
        group="small",
        size="Small",
        url="https://example.com/",
        overall_score=58,
        free_evidence_score=42,
        checked_score=42,
        checked_max=56,
        # Default-scope strict agent-readiness (protocols/account/commerce all off):
        # earned=7, max=8 → score = round(7/8*100) = 88.
        agent_readiness_score=88,
        agent_readiness_earned=7,
        agent_readiness_max=8,
        pillar_scores=PillarScores(off_site=10, scrapability=20, seo=12),
    )
    sample_benchmark = BenchmarkComparison(
        score=42,
        checked_score=42,
        checked_max=56,
        benchmark_count=1,
        median_score=42,
        percentile=50,
        position_label="At median",
        nearest=[sample_entry],
        entries=[sample_entry],
        basis="Public fallback benchmark (snapshot 2026-07-17, blog preset).",
        snapshot_date="2026-07-17",
        caveat=(
            "Benchmark positions are relative context among public peers, "
            "not exposure proof."
        ),
    )
    sample_agent_benchmark = AgentBenchmarkComparison(
        score=88,
        earned=7,
        max=8,
        benchmark_count=1,
        median_score=88,
        percentile=50,
        position_label="At median",
        nearest=[sample_entry],
        entries=[sample_entry],
        basis="Public fallback agent-readiness benchmark.",
        snapshot_date="2026-07-17",
        caveat=(
            "Strict agent-readiness positions are relative context, not "
            "exposure proof."
        ),
    )
    sample_agent = AgentReadinessSummary(
        score=88,
        earned=7,
        max=8,
        label="Developing agent-native readiness",
        categories=[
            AgentReadinessCategory(
                name="Discovery",
                earned=2,
                max=5,
                score=40,
                passed=["Public robots.txt present"],
                missing=["Public llms.txt not found"],
                excluded=[],
            )
        ],
        passed=["Public robots.txt present"],
        missing=["Public llms.txt not found"],
        not_checked=[],
        benchmark=sample_agent_benchmark,
        caveat=(
            "Strict agent-readiness measures explicit agent-native signals "
            "and does not imply citation share or agent routing."
        ),
    )
    sample_result = AuditResult(
        api_version="1.0",
        url="https://example.com/",
        scope={
            "include_protocols": False,
            "include_account_auth": False,
            "include_ecommerce": False,
            "label": "Blog/Content audit",
            "included_optional_surfaces": [],
            "excluded_optional_surfaces": [
                "protocol",
                "account_auth",
                "ecommerce",
            ],
            "preset_applied": "blog",
            "overrides_applied": {},
            "included_families": ["feed_discovery", "article_schema"],
            "excluded_families": [],
            "machine_surfaces_scope": "common-contextual",
        },
        overall_score=42,
        pillar_scores=PillarScores(off_site=10, scrapability=20, seo=12),
        pillar_max=PillarMax(off_site=30, scrapability=40, seo=30),
        agent_readiness=sample_agent,
        benchmark=sample_benchmark,
        checks=[sample_check],
    )
    return sample_result.model_dump(mode="json")


_AUDIT_ERROR_RESPONSES: dict[int | str, dict] = {
    200: {
        "model": AuditResult,
        "description": (
            "Audit completed successfully. The body is the full public "
            "AuditResult contract: api_version, resolved scope, overall "
            "score, pillar scores, strict agent-readiness summary, public "
            "benchmark comparison, and per-check group evidence."
        ),
        "content": {
            "application/json": {
                "example": _audit_200_example(),
            },
        },
    },
    400: {
        "model": ErrorMessage,
        "description": (
            "The submitted URL is syntactically valid but resolves to a "
            "blocked target (private, loopback, link-local, or reserved "
            "address range). Retry with a public HTTP(S) endpoint."
        ),
        "content": {
            "application/json": {
                "example": {
                    "detail": (
                        "URL resolves to a private address. Retry with a "
                        "public HTTP(S) endpoint."
                    )
                },
            },
        },
    },
    422: {
        "model": ValidationErrorMessage,
        "description": (
            "Request body or URL failed validation. The ``errors`` field "
            "carries the structured FastAPI 422 details or preset override "
            "failures; ``detail`` carries the human-readable summary."
        ),
        "content": {
            "application/json": {
                "examples": {
                    "missing_url": {
                        "summary": "Missing required url field",
                        "value": {
                            "detail": "Request body failed validation.",
                            "errors": [
                                {
                                    "loc": ["body", "url"],
                                    "msg": "Field required",
                                    "type": "missing",
                                }
                            ],
                        },
                    },
                    "unknown_preset": {
                        "summary": "Unknown preset identifier",
                        "value": {
                            "detail": (
                                "Unknown preset 'foo'; valid presets: "
                                "['blog', 'corporate', 'custom', 'ecommerce', "
                                "'news', 'saas', 'services']"
                            ),
                            "errors": [
                                {
                                    "loc": ["body", "preset"],
                                    "msg": "Value error, Unknown preset 'foo'",
                                    "type": "value_error",
                                }
                            ],
                        },
                    },
                    "preset_override_conflict": {
                        "summary": "Custom override rejected for preset",
                        "value": {
                            "detail": (
                                "Override 'api_catalog' is not applicable "
                                "for preset 'blog'"
                            ),
                            "errors": [
                                {
                                    "loc": ["body", "custom_overrides"],
                                    "msg": (
                                        "Override 'api_catalog' is not "
                                        "applicable for preset 'blog'"
                                    ),
                                    "type": "preset_config_incompatible",
                                }
                            ],
                        },
                    },
                },
            },
        },
    },
    429: {
        "model": RateLimitErrorMessage,
        "description": (
            "Rate limit exceeded. ``retry_after`` mirrors the ``Retry-After`` "
            "and ``X-RateLimit-Reset`` headers."
        ),
        "content": {
            "application/json": {
                "example": {
                    "detail": (
                        "Rate limit exceeded. Retry after the time "
                        "indicated by Retry-After."
                    ),
                    "retry_after": 60,
                },
            },
        },
    },
}

_AUDIT_SUMMARY_RESPONSES: dict[int | str, dict] = {
    200: {
        "model": AuditSummary,
        "description": (
            "Audit completed successfully. The body is the compact, "
            "agent-oriented AuditSummary projection with scores, benchmark "
            "context, check counts, up to five attention rows, and stable "
            "limitation codes."
        ),
        "content": {
            "application/json": {
                "example": build_report_summary(
                    AuditResult.model_validate(_audit_200_example())
                ).model_dump(mode="json", by_alias=True),
            },
        },
    },
    **{
        status: error_response
        for status, error_response in _AUDIT_ERROR_RESPONSES.items()
        if status != 200
    },
}


async def _execute_audit(body: AuditRequest) -> AuditResult:
    """Run the canonical Essentials audit pipeline once."""

    body = AuditRequest.model_validate(body)
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        url = validate_url(url)
    except ValueError as exc:
        _raise_url_http_error(str(exc))

    try:
        if body.preset is not None:
            errors = validate_overrides(body.preset, body.custom_overrides)
            if errors:
                _raise_validation_http_error(
                    "; ".join(errors),
                    errors=[
                        {
                            "loc": ["body", "custom_overrides"],
                            "msg": "; ".join(errors),
                            "type": "preset_config_incompatible",
                        }
                    ],
                )
        scope = resolve_scope(
            body.preset,
            body.custom_overrides,
            body.include_protocols,
            body.include_account_auth,
            body.include_ecommerce,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        _raise_validation_http_error(str(exc))

    logger.info(
        "audit started: %s preset=%s include_protocols=%s include_account_auth=%s include_ecommerce=%s",
        url,
        scope.preset,
        scope.include_protocols,
        scope.include_account_auth,
        scope.include_ecommerce,
    )
    context = await _build_context_or_error(url)
    checks, agent_readiness = await asyncio.gather(
        _run_essential_checks(context, body),
        _safe_agent_readiness_summary(
            context,
            scope.include_protocols,
            scope.include_account_auth,
            scope.include_ecommerce,
            logger=logger,
        ),
    )
    result = build_result(url, checks, agent_readiness, scope=scope)
    logger.info("audit complete: %s score=%d", url, result.overall_score)
    return result


def _inject_rate_limit_headers(request: Request, response: Response) -> None:
    view_rate_limit = getattr(request.state, "view_rate_limit", None)
    if view_rate_limit is not None:
        request.app.state.limiter._inject_headers(response, view_rate_limit)


@app.post(
    "/v1/audit",
    response_model=AuditResult,
    responses=_AUDIT_ERROR_RESPONSES,
    tags=["Audit"],
    summary="Run an Essentials audit",
    description=(
        "Run a free MachineRead Essentials audit against a public HTTP(S) URL. "
        "Returns the full public contract including the ``api_version`` field, "
        "pillar scores, benchmark comparison, strict agent-readiness summary, "
        "and per-check evidence. Rate-limit headers (`X-RateLimit-Limit`, "
        "`X-RateLimit-Remaining`, `X-RateLimit-Reset`) are returned on every "
        "response and `Retry-After` is added on 429."
    ),
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "minimal": {
                            "summary": "Minimal request",
                            "value": {"url": "https://example.com/"},
                        },
                        "preset_blog": {
                            "summary": "Preset-based request",
                            "value": {
                                "url": "https://example.com/",
                                "preset": "blog",
                            },
                        },
                        "custom_overrides": {
                            "summary": "Custom/Power User request",
                            "value": {
                                "url": "https://example.com/",
                                "preset": "custom",
                                "custom_overrides": {
                                    "feed_discovery": True,
                                    "api_catalog": True,
                                },
                            },
                        },
                        "legacy_booleans": {
                            "summary": "Legacy boolean scope (deprecated)",
                            "value": {
                                "url": "https://example.com/",
                                "include_protocols": True,
                                "include_account_auth": False,
                                "include_ecommerce": False,
                            },
                        },
                    },
                },
            },
            "required": True,
        },
    },
)
@app.post(
    "/audit",
    response_model=AuditResult,
    responses=_AUDIT_ERROR_RESPONSES,
    include_in_schema=False,
    summary="Run an Essentials audit (deprecated alias)",
    description=(
        "Compatibility alias for `POST /v1/audit`. Returns the same response "
        "shape and rate-limit headers. Prefer `/v1/audit` for new integrations."
    ),
    deprecated=True,
)
@limiter.shared_limit(audit_rate_limit(), scope="audit")
async def audit(request: Request, body: AuditRequest, response: Response) -> AuditResult:
    result = await _execute_audit(body)
    _inject_rate_limit_headers(request, response)
    return result


@app.post(
    "/v1/audit/summary",
    response_model=AuditSummary,
    responses=_AUDIT_SUMMARY_RESPONSES,
    tags=["Audit"],
    summary="Run an Essentials audit and return a compact summary",
    description=(
        "Run the same canonical Essentials pipeline as `POST /v1/audit`, then "
        "return a compact deterministic projection for agent consumption. The "
        "summary excludes finding/fix prose, peer entries, raw context, and "
        "agent-readiness passed/missing lists. This route shares the audit rate-"
        "limit bucket with `/v1/audit` and the deprecated `/audit` alias."
    ),
)
@limiter.shared_limit(audit_rate_limit(), scope="audit")
async def audit_summary(
    request: Request,
    body: AuditRequest,
    response: Response,
) -> AuditSummary:
    result = await _execute_audit(body)
    _inject_rate_limit_headers(request, response)
    return build_report_summary(result)
