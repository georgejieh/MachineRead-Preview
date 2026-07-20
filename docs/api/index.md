# MachineRead API Reference

This is the public, machine-readable reference for the MachineRead audit API.
It documents the public contract emitted by the free MachineRead Essentials
audit. Use the OpenAPI 3.1 JSON schema at
[`openapi.json`](./openapi.json) for code generation, schema validation, and
agent tool discovery.

This reference is the authoritative public API contract. When it disagrees
with the runtime, the runtime is the source of truth and this file should
be updated to match.

## Overview

The MachineRead API exposes two free audit response surfaces plus an operational
health probe. `POST /v1/audit` returns the full report, while
`POST /v1/audit/summary` runs the same audit pipeline and returns a compact,
deterministic projection for agents. Everything else (auth, persistence,
billing, paid crawlers, BYOP provider access, and state-changing public agent
hooks) is **not yet public** at this phase.

- Base URLs:
  - Local development: `http://127.0.0.1:8000`
  - Public production: `https://api.machineread.ai` (reserved future domain; not yet live — when the production deployment ships, this is the canonical hostname)
- Transport: HTTPS for production, JSON request and JSON response.
- Auth: None. The public API is anonymous and free.
- Versioning: All audit responses carry an `api_version` field
  (currently `"1.0"`). The version-prefixed route `POST /v1/audit` is the
  stable, recommended path; the unversioned `POST /audit` is a deprecated
  alias kept for existing integrations.
- Scope model: Each request is resolved into a single `AuditScope` that
  controls which check families and scoring rows are included. Scope is
  resolved from a `preset` (recommended) or the legacy `include_*` booleans.
- Method: Pydantic-validated JSON request bodies. `application/json` only.
  Other content types are not supported.

## Endpoints

### `POST /v1/audit`

Run the free MachineRead Essentials audit against a public HTTP(S) URL.
Returns the full public contract: api version, resolved scope, overall score,
pillar scores, strict agent-readiness summary, public benchmark comparison,
and per-check group evidence.

The unversioned `POST /audit` route is a deprecated alias with the same
request, response, error, and rate-limit contract. Use `/v1/audit` for new
integrations.

| Element | Value |
| --- | --- |
| Tags | `Audit` |
| Operation | Stable since API version `1.0` |
| Request body | `AuditRequest` (JSON, required) |
| 200 response | `AuditResult` |
| Errors | `400`, `422`, `429` |
| Auth | None |
| Rate limit | Per client IP; see [Rate limiting](#rate-limiting) |

#### Request body (`AuditRequest`)

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `url` | string | yes | Public HTTP(S) URL to audit. The scheme is added automatically if missing (defaults to `https`). Private, loopback, link-local, and reserved address ranges are rejected before the audit starts. |
| `preset` | string \| null | no | Recommended scope selector. One of `'blog'`, `'corporate'`, `'services'`, `'ecommerce'`, `'news'`, `'saas'`, `'custom'`. When set, the preset wins over the three legacy `include_*` booleans. |
| `custom_overrides` | object \| null | no | Per-feature overrides applied on top of `preset`. Requires `preset='custom'` or another preset to be set; rejected when `preset` is `None`. Keys include `protocols`, `account_auth`, `ecommerce`, `feed_discovery`, `article_schema`, `localbusiness_schema`, `news_article_schema`, `claimreview_schema`, `product_offer_schema`, `commerce_fields`, `api_catalog`, `mcp`, `a2a`, `agent_skills`, `webmcp`, `oauth_oidc`, `ard_catalog`, `auth_md`. |
| `include_protocols` | boolean | no (deprecated) | Legacy scope toggle for protocol/API surfaces (MCP, A2A, Agent Skills, WebMCP, API catalog). Prefer the preset path; this field is preserved for backward compatibility. |
| `include_account_auth` | boolean | no (deprecated) | Legacy scope toggle for account/auth surfaces (OAuth/OIDC discovery, `auth.md`). Prefer the preset path. |
| `include_ecommerce` | boolean | no (deprecated) | Legacy scope toggle for commerce surfaces (Product/Offer JSON-LD, price/availability, checkout-protocol metadata). Prefer the preset path. |

##### Examples

```json
{
  "url": "https://example.com/"
}
```

```json
{
  "url": "https://example.com/",
  "preset": "blog"
}
```

```json
{
  "url": "https://example.com/",
  "preset": "custom",
  "custom_overrides": {
    "feed_discovery": true,
    "api_catalog": true
  }
}
```

```json
{
  "url": "https://example.com/",
  "include_protocols": true,
  "include_account_auth": false,
  "include_ecommerce": false
}
```

#### Response 200 (`AuditResult`)

The successful response is a JSON object with the following top-level fields:

| Field | Type | Description |
| --- | --- | --- |
| `api_version` | string | The API version this response was generated against. Currently `"1.0"`. |
| `url` | string | The audited URL (with the normalised scheme and trailing-slash form used for fetching). |
| `scope` | object | The resolved `AuditScope`: preset applied, optional `custom_overrides` applied, the booleans that actually took effect, included/excluded optional surfaces, and which check families were scored. |
| `overall_score` | integer | Full-rubric overall score for the site (see [Scoring caveats](#scoring-caveats)). |
| `pillar_scores` | object | Pillar-level scores: `off_site`, `scrapability`, `seo`. |
| `pillar_max` | object | Maximum possible score for each pillar in this run. |
| `agent_readiness` | object | Strict agent-readiness summary: `score`, `earned`, `max`, label, per-category breakdown, lists of passed/missing/not-checked probes, and a benchmark comparison. |
| `benchmark` | object | Public-benchmark comparison: peer count, median, percentile, position label, nearest peer entries, basis string, snapshot date, and caveat. |
| `checks` | array | Per-check-group evidence (one row per Essentials group, see [Per-check evidence](#per-check-evidence)). |

##### Example 200 body (trimmed)

```json
{
  "api_version": "1.0",
  "url": "https://example.com/",
  "scope": {
    "include_protocols": false,
    "include_account_auth": false,
    "include_ecommerce": false,
    "label": "Blog/Content audit",
    "included_optional_surfaces": [],
    "excluded_optional_surfaces": ["protocol", "account_auth", "ecommerce"],
    "preset_applied": "blog",
    "overrides_applied": {},
    "included_families": ["feed_discovery", "article_schema"],
    "excluded_families": [],
    "machine_surfaces_scope": "common-contextual"
  },
  "overall_score": 42,
  "pillar_scores": { "off_site": 10, "scrapability": 20, "seo": 12 },
  "pillar_max": { "off_site": 30, "scrapability": 40, "seo": 30 },
  "agent_readiness": { "score": 88, "earned": 7, "max": 8, "label": "Developing agent-native readiness", "categories": [], "passed": [], "missing": [], "not_checked": [], "benchmark": {}, "caveat": "" },
  "benchmark": {
    "score": 42,
    "checked_score": 42,
    "checked_max": 56,
    "benchmark_count": 1,
    "median_score": 42,
    "percentile": 50,
    "position_label": "At median",
    "nearest": [],
    "entries": [],
    "basis": "Public fallback benchmark (snapshot 2026-07-17, blog preset).",
    "snapshot_date": "2026-07-17",
    "caveat": "Benchmark positions are relative context among public peers, not exposure proof."
  },
  "checks": [
    {
      "pillar": "scrapability",
      "check_name": "robots_txt",
      "label": "AI Bot Policy Signals",
      "state": "partial",
      "evidence_level": "verified",
      "score": 4,
      "max_score": 6,
      "finding": "robots.txt mentions GPTBot but does not mention ClaudeBot.",
      "fix": "Add an explicit ClaudeBot directive (Allow or Disallow) to robots.txt.",
      "effort": "low"
    }
  ]
}
```

The full response schema is in
[`openapi.json`](./openapi.json) under `components.schemas.AuditResult` and the
related `CheckResult`, `PillarScores`, `PillarMax`, `BenchmarkComparison`,
`BenchmarkEntry`, `AgentBenchmarkComparison`,
`AgentReadinessSummary`, and `AgentReadinessCategory` schemas.

### `POST /v1/audit/summary`

Run the same canonical Essentials pipeline as `POST /v1/audit`, then return the
strict `AuditSummary` projection. The request body and `400`, `422`, `429`, and
`500` behavior match the full endpoint. The full route, summary route, and the
deprecated `/audit` alias share one SlowAPI bucket per client IP, so changing
routes does not bypass the configured budget.

The summary response is deterministic and contains no generated prose. It
preserves source score denominators, benchmark medians/percentiles, compact scope
metadata, check-state counts, and up to five attention rows. It deliberately
omits full `finding` and `fix` prose, labels, benchmark peer entries, benchmark
basis/caveat prose, agent-readiness passed/missing/not-checked lists, raw fetch
context, and rate-limit metadata from the body.

| Element | Value |
| --- | --- |
| Tags | `Audit` |
| Request body | `AuditRequest` (same as `/v1/audit`) |
| 200 response | `AuditSummary` |
| Errors | `400`, `422`, `429`, `500` |
| Auth | None |
| Rate limit | Shared audit bucket per client IP |

The top-level summary fields are `api_version`, `summary_version`, `url`,
`scope`, `scores`, `benchmarks`, `checks`, `attention`, and `limitations`.
`attention` is capped at five and sorted by severity (`fail`, `partial`, `warn`),
then ascending earned/max ratio, then `check_name`. The fixed limitation codes
are `relative_scores`, `no_live_ranking`, `no_provider_ip_auth`, and
`no_paid_crawlers`.

```json
{
  "api_version": "1.0",
  "summary_version": "1.0",
  "url": "https://example.com/",
  "scope": {
    "preset": "blog",
    "protocols": false,
    "account_auth": false,
    "ecommerce": false,
    "overrides": {}
  },
  "scores": {
    "overall": { "earned": 42, "max": 100 },
    "pillars": {
      "off_site": { "earned": 10, "max": 30 },
      "scrapability": { "earned": 20, "max": 40 },
      "seo": { "earned": 12, "max": 30 }
    },
    "essentials": { "percent": 42, "earned": 42, "max": 56 },
    "agent_readiness": { "percent": 88, "earned": 7, "max": 8 }
  },
  "benchmarks": {
    "essentials": {
      "percentile": 50,
      "median_percent": 42,
      "peer_count": 1,
      "snapshot": "2026-07-17"
    },
    "agent_readiness": {
      "percentile": 50,
      "median_percent": 88,
      "peer_count": 1,
      "snapshot": "2026-07-17"
    }
  },
  "checks": {
    "included": 13,
    "locked": 9,
    "pass": 2,
    "partial": 11,
    "fail": 0,
    "warn": 0,
    "attention_total": 5
  },
  "attention": [
    {
      "check_name": "robots_txt",
      "pillar": "scrapability",
      "state": "partial",
      "evidence_level": "verified",
      "earned": 4,
      "max": 6,
      "effort": "low"
    }
  ],
  "limitations": [
    "relative_scores",
    "no_live_ranking",
    "no_provider_ip_auth",
    "no_paid_crawlers"
  ]
}
```

### `GET /health`

Operational health probe used by uptime monitors and orchestration tooling. Safe
to call without authentication and never returns customer data.

| Element | Value |
| --- | --- |
| Tags | `System` |
| Request body | None |
| 200 response | `{"status": "ok"}` |
| Auth | None |
| Rate limit | None |

```json
{ "status": "ok" }
```

## Rate limiting

The full audit, compact summary, and deprecated alias are rate-limited per
client IP by SlowAPI and share one logical `audit` bucket. The default limit is
`3/minute`, configurable via the `MACHINEREAD_AUDIT_RATE_LIMIT` environment
variable on the server. `/health` is not rate-limited.

Every response from `/v1/audit`, `/v1/audit/summary`, and `/audit` carries the
standard rate-limit headers, and `Retry-After` is added on a 429 response:

| Header | Meaning |
| --- | --- |
| `X-RateLimit-Limit` | The configured rate limit string for the current endpoint. |
| `X-RateLimit-Remaining` | Requests still permitted in the current window. |
| `X-RateLimit-Reset` | Seconds until the current window resets. |
| `Retry-After` | Seconds the client should wait before retrying (429 only). |

Clients should:

1. Read `X-RateLimit-Remaining` after every successful call and back off when
   it reaches zero.
2. On a 429 response, wait at least `Retry-After` seconds before retrying.
3. Avoid bursty retry loops. Exponential backoff is appropriate.

The 429 response body additionally carries a `retry_after` integer that
mirrors the `Retry-After` header, so clients that consume only the JSON body
do not have to read headers.

## Error codes

The API emits structured error responses with stable JSON bodies. Every
error body conforms to one of `ErrorMessage`, `ValidationErrorMessage`, or
`RateLimitErrorMessage` defined in
[`openapi.json`](./openapi.json).

| Status | Model | When |
| --- | --- | --- |
| `400` | `ErrorMessage` | The submitted URL is syntactically valid but resolves to a blocked target (private, loopback, link-local, or reserved address range). Retry with a public HTTP(S) endpoint. |
| `422` | `ValidationErrorMessage` | Request body, URL syntax, preset, or preset override failed validation. The `errors` field carries the structured FastAPI 422 details or preset override failures; `detail` carries the human-readable summary. |
| `429` | `RateLimitErrorMessage` | Rate limit exceeded. `retry_after` mirrors the `Retry-After` and `X-RateLimit-Reset` headers. |
| `500` | n/a | Internal audit-setup error. Retry the scan; if it repeats, treat the result as inconclusive rather than as verified evidence about the site. |

### Sample error bodies

```json
{
  "detail": "URL resolves to a private address. Retry with a public HTTP(S) endpoint."
}
```

```json
{
  "detail": "Request body failed validation.",
  "errors": [
    {
      "loc": ["body", "url"],
      "msg": "Field required",
      "type": "missing"
    }
  ]
}
```

```json
{
  "detail": "Unknown preset 'foo'; valid presets: ['blog', 'corporate', 'custom', 'ecommerce', 'news', 'saas', 'services']",
  "errors": [
    {
      "loc": ["body", "preset"],
      "msg": "Value error, Unknown preset 'foo'",
      "type": "value_error"
    }
  ]
}
```

```json
{
  "detail": "Override 'api_catalog' is not applicable for preset 'blog'",
  "errors": [
    {
      "loc": ["body", "custom_overrides"],
      "msg": "Override 'api_catalog' is not applicable for preset 'blog'",
      "type": "preset_config_incompatible"
    }
  ]
}
```

```json
{
  "detail": "Rate limit exceeded. Retry after the time indicated by Retry-After.",
  "retry_after": 60
}
```

## Per-check evidence

Each entry of the `checks` array is a `CheckResult` row. Public schema:

| Field | Type | Description |
| --- | --- | --- |
| `pillar` | string | One of `off_site`, `scrapability`, `seo`. |
| `check_name` | string | Stable check identifier (for example `robots_txt`, `bot_access`, `html_structure`, `schema_ld`, `llms_txt`, `ssr`, `machine_surfaces`, `pagespeed`, `canonical`, `indexing`, `search_discovery`, `social`, `wikipedia`). |
| `label` | string | Short human-readable label for the row. |
| `state` | string | One of `pass`, `partial`, `fail`, `warn`, `locked`. `warn` means the check could not be completed during the audit (the row is inconclusive rather than verified evidence). `locked` indicates paid/private coverage not exposed via the free API. |
| `evidence_level` | string | Maturity label for the row evidence. |
| `score` | integer | Points earned by this row. |
| `max_score` | integer | Points possible for this row in the resolved scope. |
| `finding` | string | What the audit observed for this row. |
| `fix` | string | Concrete remediation hint for the row. |
| `effort` | string | Expected effort to apply the fix: `low`, `medium`, or `high`. |

The free Essentials audit currently exposes 13 included check groups with a
56-point checked maximum when the default blog scope is applied. Strict
agent-readiness probes overlap some rows; the resolved rubric and overlap
table are encoded in the runtime source.

## Scoring caveats

The `overall_score` is the **full-rubric** site score; it is *not* a
citation-share, traffic, search ranking, social traction, conversion, or
field Core Web Vitals measurement. Benchmark positions are relative context
among public peers, not exposure proof. Strict agent-readiness scores measure
explicit agent-native signals (robots, `llms.txt`, machine surfaces, etc.)
and do not imply agent routing or citation share.

When a check cannot complete, the row is reported with `state: "warn"`, a
zero score, and a `fix` that suggests retrying the audit and reviewing logs
before relying on the score. Treat `warn` rows as **inconclusive**, not as
verified evidence about the site.

Free Essentials does **not** verify live DuckDuckGo or Bing ranking, does
**not** authenticate provider IP ranges, does **not** call Firecrawl or any
paid crawler, and does **not** include live agent-journey execution or
prompt stress tests. Locked rows are listed with `state: "locked"` and are
not scored in the free response.

See the OpenAPI schema at [`openapi.json`](./openapi.json) and the
[`CheckResult`](./openapi.json) model definition for the full scoring
rubric, evidence-maturity labels, and what each row does and does not
measure.

## Presets and locked rows

The preset selector maps to a default scope:

| Preset | Optional surfaces by default |
| --- | --- |
| `blog` | None (feed discovery + article schema included as standard families) |
| `corporate` | None |
| `services` | None |
| `ecommerce` | `protocol`, `account/auth`, and `ecommerce` |
| `news` | None |
| `saas` | `protocol` and `account/auth` (bundled) |
| `custom` | None (requires `custom_overrides`) |

Advanced / locked rows (off-site backlinks, social traction, AI citation
share, extraction fidelity, agent task simulation, multi-engine index
coverage, Core Web Vitals, keyword competitor gap) are **not** scored by the
free Essentials API. They appear in the report with `state: "locked"` and
require paid/private coverage that is not part of the public API at this
phase.

## Versioning and stability

- The current public API version is `1.0`, surfaced as both
  `AuditResult.api_version` and `AuditSummary.api_version`. The summary contract
  also carries `summary_version: "1.0"`.
- Breaking changes require a new route prefix (for example `/v2/audit`)
  rather than in-place renames of fields.
- New optional response fields may be added without a version bump.
- The unversioned `POST /audit` route remains available as a deprecated
  alias with the same request, response, and error contract.

## Machine-readable artifacts

- [`openapi.json`](./openapi.json): OpenAPI 3.1 JSON Schema for the public
  API, suitable for code generation and OpenAPI tooling. Schema diffs
  between this file and the live runtime should be treated as drift to be
  reconciled at the next publish step.
- Live interactive docs (only available when the API is running locally):
  `GET /docs` (Swagger UI) and `GET /redoc` (ReDoc).
- Live schema endpoint (only available when the API is running locally):
  `GET /openapi.json`.

## Public/private boundary

This reference documents only what is public via the public MachineRead
Essentials audit. The following are **not** part of the public API at this
phase and are intentionally absent from this file:

- Authentication, accounts, billing, and paid-tier endpoints.
- Crawler runners, paid LLM / search integrations, and BYOP provider
  access.
- Per-customer audit history, persistence, or replay.
- Internal identifiers, infrastructure URLs, environment variable names,
  benchmark profile data, private docs, and orchestrator run logs.
- Public agent hooks beyond the discovery layer covered here
  (MCP tools, Agent Skill package, Apps SDK wrapper) — those live in
  separate docs as each phase ships.

Anything not listed in this document is private and must not be
referenced from public agents, public copy, or public code.
