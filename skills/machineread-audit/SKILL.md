---
name: machineread-audit
description: "Use when auditing a public website for AI visibility, agent accessibility, scrapability, or search-discovery readiness with the free MachineRead Essentials audit. Triggers: 'audit this URL for AI readiness', 'check if the site is agent-friendly', 'run a MachineRead audit', 'get a public-readiness score'. Calls POST /v1/audit or the public MCP server (run_essentials_audit). Returns the public 13-groups/56-points Essentials contract with caveats; never claims search ranking, traffic, backlinks, citation share, or field Core Web Vitals."
license: MIT
compatibility: "requires: python>=3.11, network access"
metadata:
  version: "1.0.0"
  author: "MachineRead"
  tags: "machineread, audit, agent-readiness, public-readiness, mcp"
---

# MachineRead Audit Skill

Use this skill when a user asks whether a public website is ready for AI assistants, LLM crawlers, and search-discovery tooling, and wants a free, evidence-based answer. The skill covers the public `machineread-audit` surface only — paid tiers, OAuth, LLM-generated reports, and benchmark comparisons against private peer profiles are out of scope.

## Overview

MachineRead is a free public-readiness scanner for websites. The product surface this skill uses is the **Essentials audit**: a deterministic check of public HTTP, DNS, robots, sitemap, HTML, structured data, and machine-readable agent surfaces, with no paid search, backlink, social, crawler, or LLM APIs.

Essentials contract (the contract this skill calls):

- **13 included Essential check groups** (10 universal core rows plus 3 contextual rows: `social`, `wikipedia`, `machine_surfaces`).
- **56-point checked maximum** across the three pillars.
- **Pillar caps:** `off_site` 30, `scrapability` 40, `seo` 30 (a 30/40/30 split).
- **Three independent score systems** in every response:
  - `overall_score` — full-rubric score, including locked advanced rows reported as unavailable.
  - `benchmark.score` — Essentials evidence score from included free rows only.
  - `agent_readiness.score` — a stricter agent-native lens with an 8-probe default scope and a 21-probe full scope when protocol, account/auth, and commerce surfaces are all enabled.

The audit is intentionally evidence-based and deterministic. The free tier never calls paid crawlers, never authenticates provider IPs, and never verifies live ranking or citation share.

## When to Use

Use this skill when the user says or implies any of:

- "Audit this URL for AI readiness" or "Check if the site is agent-friendly".
- "Run a MachineRead audit" or "Get a public-readiness score".
- "How ready is this site for LLM crawlers?" or "Will AI assistants see this site?".
- "Check robots.txt, llms.txt, JSON-LD, and machine surfaces for `<url>`."
- "Score this site on the MachineRead Essentials audit."

Do **not** use this skill for:

- Paid-tier or authenticated work (no OAuth, no accounts, no per-customer history).
- LLM-generated prose reports (Essentials is structured; no narrative summaries).
- Citing a MachineRead score as proof of search ranking, citation share, traffic, conversion, or field Core Web Vitals.
- Sites behind authentication, intranet endpoints, or anything not reachable over public HTTP(S).

## Quick Start

Pick the path that matches the host environment. Always use `POST /v1/audit` (versioned), never `/audit` (deprecated alias).

### HTTP API

```bash
curl -sS -X POST "$MACHINEREAD_API/v1/audit" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/"}'
```

Default host: `https://api.machineread.ai` (reserved future production domain; not yet live). Override with the `MACHINEREAD_API` environment variable (no trailing slash).

### MCP server (stdio)

If the host has the MachineRead MCP server configured locally:

```json
{
  "method": "tools/call",
  "params": {
    "name": "run_essentials_audit",
    "arguments": { "url": "https://example.com/", "preset": "blog" }
  }
}
```

Launch: The MachineRead MCP server is maintained in the private product repository and is not part of this public preview. See `docs/api/mcp-server-card.json` and `docs/api/mcp.md` for the public discovery metadata, and `references/mcp-tools.md` for the tool contract.

### Helper script

```bash
python scripts/run_audit.py https://example.com/
python scripts/run_audit.py https://example.com/ --preset saas
python scripts/run_audit.py https://example.com/ --field overall_score
```

Stdlib only; no third-party dependencies. See `references/examples.md` for full call shapes.

## Choosing the Preset

Pick the preset that best matches the site type. If unsure, default to `corporate`. The preset decides which check families and which strict agent-readiness probes are in scope.

| Preset | Use for | Default optional surfaces |
| --- | --- | --- |
| `blog` | Personal blogs, content sites, newsletters, static site generators | None (article schema, feeds, speakable already on) |
| `corporate` | Company sites, brand pages, portfolios, agency homepages | None |
| `services` | Local businesses, service providers, clinics, contractors | None (local-business schema already on) |
| `ecommerce` | Online stores, product catalogs, marketplaces | `protocol + auth + commerce` |
| `news` | News sites, magazines, publishers | None (news schema, ClaimReview already on) |
| `saas` | SaaS platforms, API products, developer tools | `protocol + auth` |
| `custom` | Power-user mode; user-selected sub-families | User-selected |

The preset maps to a benchmark scope key (`p{protocol}_a{auth}_c{commerce}`) used by the public benchmark comparison. Pick the closest preset so the comparison is meaningful.

For `ecommerce` and `saas`, the protocol surface (API Catalog, MCP, A2A, Agent Skills, WebMCP) is part of the preset default, not an optional toggle. `custom` accepts overrides via the `custom_overrides` field.

## Reading the Result

Top-level fields returned by `POST /v1/audit`:

- `api_version` — `"1.0"`.
- `url` — normalised audited URL.
- `scope` — resolved scope: preset applied, included/excluded families, machine-surface scope.
- `overall_score`, `pillar_scores`, `pillar_max` — full-rubric score and the 30/40/30 caps.
- `agent_readiness` — strict agent-readiness summary: `score`, `earned`, `max`, passed/missing/not-checked probe lists.
- `benchmark` — public-benchmark comparison: peer count, median, percentile, position label, basis string.
- `checks` — one row per Essentials group (13 included rows plus 9 locked advanced rows — up to 22 total) with `state`, `score`, `max_score`, `finding`, `fix`, `effort`.

State values: `pass`, `partial`, `fail`, `warn` (inconclusive), `locked` (paid/private coverage not exposed). See `references/api.md` for the full schema and `references/scoring-caveats.md` for the three score systems in detail.

## Caveats (Mandatory)

These caveats apply to every audit this skill produces. Repeat them in user-facing copy where the score is presented.

1. Free Essentials does **not** verify live DuckDuckGo or Bing ranking.
2. Free Essentials does **not** authenticate provider IP ranges.
3. Free Essentials does **not** call Firecrawl or any paid crawler.
4. Scores are relative public-readiness signals, not citation share, traffic, search ranking, social traction, conversion, or field Core Web Vitals.
5. Benchmark position is among public peers; comparable scores do not imply comparable real-world outcomes.

## Rate Limits and Errors

### HTTP `/v1/audit`

- **Rate limit:** 3 requests per minute per client IP. Configurable server-side.
- **Standard headers:** `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`. `Retry-After` is added on `429`.
- **Status codes:** `200`, `400` (URL resolves to blocked target), `422` (validation / preset / override failure), `429` (rate limited), `500` (internal audit setup error).

### MCP `run_essentials_audit`

- **Rate limit:** 3 requests per minute per client (fixed window, in-process).
- **Error envelope:** `{ok: false, error: {code, message}}`. Stable codes: `URL_TOO_LONG`, `URL_NOT_ALLOWED`, `INVALID_PRESET`, `RATE_LIMIT_EXCEEDED`, `AUDIT_SETUP_FAILED`, `AUDIT_NOT_FOUND`, `CHECK_NOT_FOUND`.

### Helper script (`scripts/run_audit.py`)

- **Exit codes:** `0` success, `1` HTTP 4xx, `2` CLI usage error, `3` HTTP 429, `4` HTTP 5xx, `5` connection/network error.

See `references/error-codes.md` for the full three-namespace mapping.

## Common Pitfalls

Avoid these mistakes when reporting or using Essentials results:

1. **Treating Essentials as ranking proof.** Essentials is a public-readiness signal, not a search-ranking measurement. Never write "this site will rank higher".
2. **Using `/audit` instead of `/v1/audit`.** The unversioned route is a deprecated alias; new integrations must use `/v1/audit`.
3. **Conflating `llms.txt` presence with model inclusion.** A site can publish a valid `llms.txt` and still be ignored by any specific model.
4. **Reading benchmark percentile as exposure guarantee.** Benchmark position is relative context among public peers; it is not a projection of traffic, citation share, or conversion.
5. **Re-running instead of caching the `audit_id`.** The MCP `get_audit_report` tool retrieves a cached audit by `audit_id` and avoids re-running the same URL + preset inside the LRU window.
6. **Sending private URLs.** Essentials is for public HTTP(S) targets only. Loopback, link-local, and reserved ranges are rejected before the audit starts.
7. **Conflating agent-readiness with overall score.** `agent_readiness.score` is a stricter lens on agent-native signals (robots, `llms.txt`, machine surfaces) and is not the same number as `overall_score`.
8. **Using deprecated boolean toggles.** `include_protocols`, `include_account_auth`, `include_ecommerce` remain accepted for backward compatibility but the preset selector is the recommended path.

## Verification Checklist

Before reporting a score to the user, confirm:

- [ ] URL was submitted to `POST /v1/audit` (not `/audit`).
- [ ] Preset matches the site type, or defaults to `corporate` when unsure.
- [ ] Response `api_version` is `"1.0"`.
- [ ] `overall_score` is reported alongside the matching `pillar_scores` and the 30/40/30 `pillar_max`.
- [ ] `agent_readiness.score` and its `max` (8 default, 21 full) are surfaced separately.
- [ ] `benchmark` carries the public peers basis string, not a private production basis.
- [ ] Caveats from this skill are reproduced in user-facing copy where the score is presented.
- [ ] No claim of ranking, traffic, citation share, conversion, or Core Web Vitals field data.

## References

- `references/api.md` — `/v1/audit` request/response contract and error schema.
- `references/presets.md` — seven presets, mapping to benchmark scope keys.
- `references/scoring-caveats.md` — three score systems, 13 groups, 9 locked rows, 30/40/30 caps, 21-probe full scope.
- `references/mcp-tools.md` — four MCP tools, stdio transport, structured `{ok, data|error}` envelope.
- `references/error-codes.md` — three error namespaces (HTTP, helper exit codes, MCP tool codes).
- `references/examples.md` — worked samples for HTTP, preset, and custom audits.
- `scripts/run_audit.py` — stdlib-only helper that calls `POST /v1/audit`.
