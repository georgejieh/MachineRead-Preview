# MCP Tools

The MachineRead MCP server exposes four read-only tools over stdio. There is **no hosted remote MCP endpoint** — the server runs locally and is maintained in the private MachineRead product repository. This skill ships the public discovery metadata (the MCP Server Card under `docs/api/`) and the tool contract documented here; the MCP server implementation itself is not part of this public preview.

## Transport

- **Transport:** stdio (JSON-RPC on stdin/stdout).
- **Auth:** none. The server is anonymous; no OAuth, no API keys, no per-user tokens.
- **Rate limit:** 3 requests per minute per client (fixed window, in-process).
- **Output envelope:** every tool returns `{ok, data|error}`. Success carries `data`; failure carries `error: {code, message}`.
- **Logging:** stderr only. stdout is reserved for MCP JSON-RPC frames.

## Tools

### `run_essentials_audit`

Execute a new Essentials audit against a public URL.

| Argument          | Type        | Required | Description |
| ----------------- | ----------- | -------- | ----------- |
| `url`             | string      | yes      | Public HTTP(S) URL to audit (max 2048 chars). |
| `preset`          | string/null | no       | One of `blog`, `corporate`, `services`, `ecommerce`, `news`, `saas`, `custom`. |
| `custom_overrides`| object/null | no       | Per-family overrides; requires `preset` to be set. |
| `wait_for`        | boolean     | no       | If `true` (default), poll until completion or timeout. |
| `timeout_seconds` | integer     | no       | Max seconds to wait. Default `30`, clamped to `[5, 60]`. |

Success returns `{ok: true, data: {audit_id, url, status, overall_score, pillars, agent_readiness, rate_limit, ...}}`. The `audit_id` is a SHA-256 cache key derived from canonical URL + preset; pass it to `get_audit_report` to retrieve the same audit from the in-process LRU cache.

Failure returns `{ok: false, error: {code, message}}`. Stable error codes: `URL_TOO_LONG`, `URL_NOT_ALLOWED`, `INVALID_PRESET`, `RATE_LIMIT_EXCEEDED`, `AUDIT_SETUP_FAILED`.

### `get_audit_report`

Retrieve a cached audit by its `audit_id`. Only successfully completed audits are cached. The cache is LRU bounded to 32 entries, keyed by canonical URL + preset, and lives entirely in-process.

| Argument   | Type   | Required | Description |
| ---------- | ------ | -------- | ----------- |
| `audit_id` | string | yes      | The `audit_id` returned by `run_essentials_audit`. |

Success returns the same shape as `run_essentials_audit` data. Failure returns `{ok: false, error: {code: "AUDIT_NOT_FOUND", message: ...}}`.

### `list_available_checks`

Enumerate the 13 Essentials check groups with stable identifiers, pillar (`off_site`, `scrapability`, `seo`), label, max score, applicability (universal, contextual, scoped), availability (`Essentials` for the free tier), and the option families that affect each check.

No arguments.

Success returns `{ok: true, data: {checks: [...], total_count: 13, denominator: 56, pillar_caps: {off_site: 30, scrapability: 40, seo: 30}}}`.

### `explain_check`

Return the detailed rubric for one of the 13 Essentials check names: `robots_txt`, `bot_access`, `html_structure`, `schema_ld`, `llms_txt`, `ssr`, `machine_surfaces`, `pagespeed`, `canonical`, `indexing`, `search_discovery`, `social`, `wikipedia`.

| Argument     | Type   | Required | Description |
| ------------ | ------ | -------- | ----------- |
| `check_name` | string | yes      | One of the 13 stable check identifiers above. |

Success returns detailed explanation including `sub_signals`, `scoring_rubric`, `finding_examples`, and `fix_examples`. Failure returns `{ok: false, error: {code: "CHECK_NOT_FOUND", message: ...}}`.

## Structured output envelope

```json
{
  "ok": true,
  "data": { "audit_id": "sha256...", "url": "https://example.com/", "status": "completed", "overall_score": 58 }
}
```

```json
{
  "ok": false,
  "error": { "code": "URL_NOT_ALLOWED", "message": "URL resolves to a private address: 127.0.0.1" }
}
```

## Privacy and logging

- Logs are written to **stderr only**.
- User-supplied URLs are collapsed to `[URL:hostname]` — host stays visible for operational debugging (cache hits, SSRF blocks), but credentials, paths, and query strings are stripped.
- Known token patterns (Anthropic SK keys, GitHub PATs, AWS access keys, Slack/OAuth tokens, Bearer headers) are replaced with `[TOKEN]` before emission.
- Server-derived values (`audit_id`, scores, check names, error codes) are logged as-is.
- Logs never include customer URL contents, page bodies, or extracted page text.

The default log level is `INFO`. Override with `MCP_LOG_LEVEL`.

## What is intentionally absent

- No hosted remote MCP endpoint. The server is local stdio only.
- No OAuth, no API keys, no scopes, no roles, no entitlement checks.
- No paid/authenticated tools. No `compare_benchmark_context` tool — peer profile data is private.
- No agent execution logs that store request bodies.
- No state-changing tools; all four tools are read-only.

## Companion docs

- `docs/api/mcp.md` — human-readable MCP discovery page.
- `docs/api/mcp-server-card.json` — machine-readable MCP Server Card.