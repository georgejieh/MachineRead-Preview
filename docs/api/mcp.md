# MachineRead MCP Discovery

This is the public, human-readable reference for the MachineRead Model Context
Protocol (MCP) server card. It documents the public MCP discovery surface
that exposes the free MachineRead Essentials audit to MCP-compatible agents.

The machine-readable counterpart is
[`mcp-server-card.json`](./mcp-server-card.json), which follows the experimental
MCP Server Card schema for agent discovery.

This reference describes only what is published in this repository under
`docs/api/`. The MCP server implementation is maintained in the private
MachineRead product repository and is not part of this public preview.

## Server Identity

| Element      | Value |
| ---          | --- |
| Server name  | `io.github.machineread/mcp` |
| Version      | `0.1.0` |
| Transport    | `stdio` |
| Repository   | `https://github.com/georgejieh/MachineRead` |
| Website      | `https://machineread.ai/` (reserved future production domain; not yet live) |

The namespace `io.github.machineread/mcp` is used while the
`machineread.ai` domain is reserved for production hosting but not yet live.
The reverse-DNS form `com.machineread/mcp` is reserved for the eventual switch
to the production domain and is not exposed here.

## Tools

The MCP server exposes four read-only tools. Every tool returns a structured
`{ok, data|error}` envelope; success carries `data`, failure carries
`error: {code, message}` using the stable error codes listed below.

### `run_essentials_audit`

Execute a new Essentials audit against a public HTTP(S) URL. Returns the full
public contract — `audit_id`, overall score, pillar scores against the 30/40/30
caps, strict agent-readiness summary, and per-check evidence — identical to the
HTTP API response.

| Argument        | Type | Required | Description |
| ---             | --- | --- | --- |
| `url`           | string | yes | Public HTTP(S) URL to audit (max 2048 chars). |
| `preset`        | string \| null | no | One of `blog`, `corporate`, `services`, `ecommerce`, `news`, `saas`, `custom`. |
| `custom_overrides` | object \| null | no | Per-family overrides when `preset='custom'`. |
| `wait_for`      | boolean | no | If `true` (default), poll until completion or timeout. |
| `timeout_seconds` | integer | no | Max seconds to wait. Default `30`, clamped to `[5, 60]`. |

On success: `{ok: true, data: {audit_id, url, status, overall_score, pillars,
agent_readiness, rate_limit, ...}}`. The `audit_id` is a SHA-256 cache key
derived from the canonical URL + preset and can be passed to
`get_audit_report` to retrieve the same audit from the in-process LRU cache.

### `get_audit_report`

Retrieve a cached audit by its `audit_id`. Only successfully completed audits
are cached. The cache is LRU bounded to 32 entries, keyed by canonical URL
+ preset combination, and lives entirely in-process.

| Argument   | Type | Required | Description |
| ---        | --- | --- | --- |
| `audit_id` | string | yes | The `audit_id` returned by `run_essentials_audit`. |

On success: same shape as `run_essentials_audit` data. On failure:
`{ok: false, error: {code: "AUDIT_NOT_FOUND", message: ...}}`.

### `list_available_checks`

Enumerate the 13 Essentials check groups with their stable identifiers, pillar
(`off_site`, `scrapability`, `seo`), human-readable label, max score,
applicability (universal, contextual, scoped), availability (`Essentials` for
the free tier), and the option families that affect each check.

On success:
`{ok: true, data: {checks: [...], total_count: 13, denominator: 56, pillar_caps: {off_site: 30, scrapability: 40, seo: 30}}}`.

### `explain_check`

Return the detailed rubric for one of the 13 Essentials check names:
`robots_txt`, `bot_access`, `html_structure`, `schema_ld`, `llms_txt`, `ssr`,
`machine_surfaces`, `pagespeed`, `canonical`, `indexing`, `search_discovery`,
`social`, `wikipedia`.

| Argument     | Type | Required | Description |
| ---          | --- | --- | --- |
| `check_name` | string | yes | One of the 13 stable check identifiers above. |

On success: detailed explanation including `sub_signals`, `scoring_rubric`,
`finding_examples`, and `fix_examples`. On failure:
`{ok: false, error: {code: "CHECK_NOT_FOUND", message: ...}}`.

## Authentication

Authentication is **not required** and **not supported** by the public MCP
server.

- No OAuth flow, no API key, no per-user token, no scope negotiation.
- All tools are anonymous. They cannot be elevated to a paid tier through the
  MCP surface.
- There are no scopes, no roles, and no entitlement checks at the MCP layer.
  The free Essentials contract is the only contract exposed.

Per-user OAuth, paid entitlements, and authenticated MCP variants are
intentionally absent at this phase and are tracked as private work outside the
public surface.

## Rate Limits

The server applies a fixed-window rate limit of **3 requests per minute per
client**, evaluated in-process.

- The window is a fixed minute boundary; bursts above 3/min return
  `RATE_LIMIT_EXCEEDED`.
- The scope is the in-process server state — there is no global cross-host
  limiter and no per-IP HTTP `Retry-After` style back-off, because the transport
  is stdio and the client is a local host agent.
- Every successful `run_essentials_audit` response also carries a `rate_limit`
  block describing the current limit state for that tool call.

Clients should back off briefly on `RATE_LIMIT_EXCEEDED`, avoid bursty retry
loops, and use `get_audit_report` against a cached `audit_id` whenever possible
instead of re-running the same URL + preset.

## Error Codes

All tools emit the same structured error envelope. The `code` field is stable
across versions; the `message` field is human-readable and may change.

| Code | Meaning |
| --- | --- |
| `URL_TOO_LONG` | Submitted URL exceeds the 2048-character limit. |
| `URL_NOT_ALLOWED` | URL failed SSRF validation (private, loopback, link-local, or reserved address). |
| `INVALID_PRESET` | `preset` value is not one of the seven supported preset identifiers. |
| `RATE_LIMIT_EXCEEDED` | Fixed-window limit of 3/minute per client was exceeded. |
| `AUDIT_SETUP_FAILED` | The server could not build the audit context for the submitted URL. |
| `AUDIT_NOT_FOUND` | The supplied `audit_id` is not in the in-process LRU cache. |
| `CHECK_NOT_FOUND` | The supplied `check_name` is not one of the 13 Essentials check identifiers. |

Example error envelope:

```json
{
  "ok": false,
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Rate limit exceeded. Retry after the fixed window resets."
  }
}
```

## Privacy and Logging

The server writes structured logs to **stderr only**. stdout is reserved for
MCP JSON-RPC frames; any other bytes there will corrupt the transport.

**Redaction invariant:**

- User-supplied URLs are collapsed to `[URL:hostname]` — the host stays visible
  for operational debugging (cache hits, SSRF blocks) but credentials, paths,
  and query strings are stripped before emission.
- Known token patterns (Anthropic SK keys, GitHub PATs, AWS access keys, Slack
  / OAuth tokens, Bearer headers) are replaced with `[TOKEN]` before emission.
- Server-derived values (`audit_id`, scores, check names, error codes) are
  logged as-is because they carry no customer URL contents.
- Audit IDs are preserved in logs to support cross-referencing without
  leaking the submitted URL.

The default log level is `INFO`. Override with the `MCP_LOG_LEVEL` environment
variable. Host agents (Claude Desktop, Claude Code, IDE extensions) may capture
or route stderr; the redaction guarantees apply regardless of where stderr ends
up. Logs never include customer URL contents, page bodies, or extracted page
text.

## Discovery Paths

The server card is the canonical machine-readable artifact for registry
publication and agent discovery:

- Static URL (production, reserved future domain; not yet live): `https://machineread.ai/docs/api/mcp-server-card.json`
- Source of truth (public repo): `docs/api/mcp-server-card.json`
- Companion human reference: [`docs/api/mcp.md`](./mcp.md)

When the product is hosted at `machineread.ai` (reserved future production domain; not yet live), the API catalog entry shape for
future registry publication will mirror the existing
[`docs/api/ai-catalog.json`](./ai-catalog.json) file: one `services` row
describing the MCP server alongside the HTTP audit, with `discovery`,
`auth`, `rate_limit`, and `caveats` blocks aligned to this card.

Note: `https://machineread.ai/` is the reserved production URL. It is **not
yet live**; until production hosting ships, the canonical source for the card
and reference is the public repository under `docs/api/`.

## Public/Private Boundary

This reference documents only what is public via the public MachineRead MCP
surface. The following are **not** part of the public MCP server at this phase
and are intentionally absent from this file:

- OAuth scopes, per-user tokens, and authenticated MCP variants.
- Production deployment URLs beyond the reserved `machineread.ai` placeholder.
- Internal audit IDs, in-process cache state, and per-host runtime state.
- Benchmark profile data (the private `compare_benchmark_context` benchmark
  comparator stays internal because peer profiles are private).
- Agent execution logs that store request bodies, customer URL contents, or
  extracted page text.

Anything not listed in this document is private and must not be referenced
from public agents, public copy, or public code.

## Versioning

The current public server card version is `0.1.0`. The MCP surface follows the
same additive-only rule as the rest of the public API:

- New optional tool arguments and new optional response fields may be added
  without a version bump.
- Adding a new tool requires a minor version bump of the server card.
- Renaming or removing a tool, changing a stable error code, or changing the
  tool output shape requires a new server card version and a corresponding
  reference update.
- Breaking changes to the underlying HTTP audit contract (the 13-groups /
  56-points Essentials rubric, the 30/40/30 pillar caps, or the error schema)
  propagate to the MCP surface automatically because tools share the same
  backend service layer.