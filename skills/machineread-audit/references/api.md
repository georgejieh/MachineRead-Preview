# /v1/audit Contract

Reference for the public, free `POST /v1/audit` endpoint. Source of truth is the OpenAPI 3.1 schema at `docs/api/openapi.json` and the public API reference at `docs/api/index.md`. This reference mirrors `docs/api/index.md`; when the two disagree, follow `docs/api/index.md`.

## Endpoint

| Element        | Value                                                  |
| -------------- | ------------------------------------------------------ |
| Route          | `POST /v1/audit` (versioned, recommended)              |
| Deprecated     | `POST /audit` — same contract, kept as alias           |
| Base URL       | `https://api.machineread.ai` (reserved future production domain; not yet live) |
| Local dev      | `http://127.0.0.1:8000`                                |
| Auth           | None                                                   |
| Rate limit     | 3 requests/min per client IP (configurable)            |
| Content type   | `application/json`                                     |
| 200 response   | `AuditResult`                                          |
| Errors         | `400`, `422`, `429`, `500`                             |

Always use `/v1/audit` when the full report is required. Use
`/v1/audit/summary` when a compact structured projection is sufficient.

## Compact summary endpoint

`POST /v1/audit/summary` accepts the same `AuditRequest`, runs the same audit
pipeline, and returns `AuditSummary`. Its `400`, `422`, `429`, and `500` behavior
matches `/v1/audit`. `/v1/audit`, `/v1/audit/summary`, and `/audit` share one
per-client-IP rate-limit bucket; switching routes does not create more quota.

The response carries:

- `api_version` and `summary_version` (`"1.0"`)
- compact scope booleans, preset, and overrides
- overall, pillar, Essentials, and strict agent-readiness earned/max pairs
- Essentials and agent-readiness percentile, median, peer count, and snapshot
- included/locked/pass/partial/fail/warn counts
- at most five attention rows ordered by severity, earned/max ratio, then name
- the fixed ordered limitation codes `relative_scores`, `no_live_ranking`,
  `no_provider_ip_auth`, and `no_paid_crawlers`

It excludes full `finding`/`fix` prose, display labels, benchmark entries and
nearest peers, benchmark basis/caveat prose, agent-readiness passed/missing/
not-checked lists, raw fetch context, and LLM-generated summaries.

```json
{
  "url": "https://example.com/",
  "preset": "saas"
}
```

## Request body — `AuditRequest`

| Field                  | Type             | Required | Description |
| ---------------------- | ---------------- | -------- | ----------- |
| `url`                  | string           | yes      | Public HTTP(S) URL to audit. Scheme added if missing. Loopback, link-local, reserved ranges rejected before the audit starts. Max 2048 characters. |
| `preset`               | string \| null   | no       | Recommended scope selector. One of `blog`, `corporate`, `services`, `ecommerce`, `news`, `saas`, `custom`. Wins over legacy `include_*` booleans. |
| `custom_overrides`     | object \| null   | no       | Per-family overrides applied on top of `preset`. Requires `preset` to be set. Keys include `protocols`, `account_auth`, `ecommerce`, `feed_discovery`, `article_schema`, `localbusiness_schema`, `news_article_schema`, `claimreview_schema`, `product_offer_schema`, `commerce_fields`, `api_catalog`, `mcp`, `a2a`, `agent_skills`, `webmcp`, `oauth_oidc`, `ard_catalog`, `auth_md`. |
| `include_protocols`    | boolean          | no (deprecated) | Legacy scope toggle for protocol/API surfaces. Prefer the preset path. |
| `include_account_auth` | boolean          | no (deprecated) | Legacy scope toggle for account/auth surfaces. Prefer the preset path. |
| `include_ecommerce`    | boolean          | no (deprecated) | Legacy scope toggle for commerce surfaces. Prefer the preset path. |

### Minimal request

```json
{ "url": "https://example.com/" }
```

### Preset request

```json
{ "url": "https://example.com/", "preset": "saas" }
```

### Custom preset with overrides

```json
{
  "url": "https://example.com/",
  "preset": "custom",
  "custom_overrides": { "feed_discovery": true, "api_catalog": true }
}
```

### Legacy boolean request

```json
{
  "url": "https://example.com/",
  "include_protocols": true,
  "include_account_auth": false,
  "include_ecommerce": false
}
```

## Response — `AuditResult`

| Field             | Type     | Description |
| ----------------- | -------- | ----------- |
| `api_version`     | string   | API version, currently `"1.0"`. |
| `url`             | string   | Normalised audited URL. |
| `scope`           | object   | Resolved `AuditScope`: preset applied, overrides applied, included/excluded families. |
| `overall_score`   | integer  | Full-rubric score (0–100). |
| `pillar_scores`   | object   | Pillar scores: `off_site`, `scrapability`, `seo`. |
| `pillar_max`      | object   | Pillar max: `{off_site: 30, scrapability: 40, seo: 30}`. |
| `agent_readiness` | object   | Strict agent-readiness summary: `score`, `earned`, `max`, label, passed/missing/not-checked probes. |
| `benchmark`       | object   | Public-benchmark comparison: peer count, median, percentile, position label, basis string. |
| `checks`          | array    | One `CheckResult` per Essentials group (13 included) plus 9 locked advanced rows — up to 22 total. |

### Trimmed 200 example

```json
{
  "api_version": "1.0",
  "url": "https://example.com/",
  "scope": {
    "include_protocols": false,
    "include_account_auth": false,
    "include_ecommerce": false,
    "label": "Corporate/Brand audit",
    "preset_applied": "corporate",
    "overrides_applied": {},
    "included_families": [],
    "excluded_families": [],
    "machine_surfaces_scope": "common-contextual"
  },
  "overall_score": 42,
  "pillar_scores": { "off_site": 10, "scrapability": 20, "seo": 12 },
  "pillar_max": { "off_site": 30, "scrapability": 40, "seo": 30 },
  "agent_readiness": {
    "score": 88, "earned": 7, "max": 8,
    "label": "Developing agent-native readiness"
  },
  "benchmark": {
    "score": 42, "checked_score": 42, "checked_max": 56,
    "median_score": 42, "percentile": 50,
    "position_label": "At median",
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
      "score": 4, "max_score": 6,
      "finding": "robots.txt mentions GPTBot but does not mention ClaudeBot.",
      "fix": "Add an explicit ClaudeBot directive (Allow or Disallow) to robots.txt.",
      "effort": "low"
    }
  ]
}
```

## Error schema

All error responses follow `ErrorMessage`, `ValidationErrorMessage`, or `RateLimitErrorMessage`. See `error-codes.md`.

```json
{ "detail": "URL resolves to a private address. Retry with a public HTTP(S) endpoint." }
```

```json
{ "detail": "Request body failed validation.", "errors": [{ "loc": ["body", "url"], "msg": "Field required", "type": "missing" }] }
```

```json
{ "detail": "Rate limit exceeded.", "retry_after": 60 }
```
