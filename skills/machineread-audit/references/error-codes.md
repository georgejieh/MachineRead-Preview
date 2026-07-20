# Error Codes

Three distinct error namespaces are used by the MachineRead public surface. They do not share codes or status models.

1. **HTTP** — status codes on `POST /v1/audit` (and the deprecated `/audit` alias).
2. **Helper script** — exit codes returned by `../scripts/run_audit.py`.
3. **MCP tool** — stable `code` strings inside the `{ok: false, error: {code, message}}` envelope.

## HTTP status codes (`POST /v1/audit`)

| Status | Body model             | When |
| -----: | ---------------------- | ---- |
| `200`  | `AuditResult`          | Audit completed successfully. |
| `400`  | `ErrorMessage`         | URL resolves to a blocked target (private, loopback, link-local, or reserved range). Retry with a public HTTP(S) endpoint. |
| `422`  | `ValidationErrorMessage`| Request body, URL syntax, preset, or preset override failed validation. `errors` carries the structured FastAPI 422 details or preset override failures; `detail` carries the human-readable summary. |
| `429`  | `RateLimitErrorMessage`| Rate limit exceeded. `retry_after` mirrors the `Retry-After` and `X-RateLimit-Reset` headers. |
| `500`  | n/a                    | Internal audit-setup error. Retry the scan; if it repeats, treat the result as inconclusive rather than as verified evidence about the site. |

### Standard headers on every response

| Header                | Meaning |
| --------------------- | ------- |
| `X-RateLimit-Limit`   | The configured rate limit string for the current endpoint. |
| `X-RateLimit-Remaining` | Requests still permitted in the current window. |
| `X-RateLimit-Reset`   | Seconds until the current window resets. |
| `Retry-After`         | Seconds the client should wait before retrying (`429` only). |

### Sample 422 with preset error

```json
{
  "detail": "Unknown preset 'foo'; valid presets: ['blog', 'corporate', 'custom', 'ecommerce', 'news', 'saas', 'services']",
  "errors": [
    { "loc": ["body", "preset"], "msg": "Value error, Unknown preset 'foo'", "type": "value_error" }
  ]
}
```

### Sample 422 with override error

```json
{
  "detail": "Override 'api_catalog' is not applicable for preset 'blog'",
  "errors": [
    { "loc": ["body", "custom_overrides"], "msg": "Override 'api_catalog' is not applicable for preset 'blog'", "type": "preset_config_incompatible" }
  ]
}
```

## Helper script exit codes (`../scripts/run_audit.py`)

The helper wraps `POST /v1/audit` and translates HTTP outcomes into exit codes so callers can branch on them in shell pipelines.

| Exit code | Meaning |
| --------: | ------- |
| `0`       | Success — audit completed; response data was printed to stdout. |
| `1`       | HTTP `4xx` — validation or SSRF rejection (except `429`). |
| `2`       | CLI usage error — bad arguments, override JSON failed to decode, invalid URL scheme, URL too long. |
| `3`       | HTTP `429` — rate limit exceeded. Wait at least `Retry-After` seconds before retrying. |
| `4`       | HTTP `5xx` — server-side audit-setup error. Retry once; if it repeats, treat as inconclusive. |
| `5`       | Connection / network error — DNS, TLS, or socket failure reaching the API host. |

Diagnostics are written to **stderr only**. Response data is written to **stdout** as JSON (pretty-printed by default; use `--no-pretty` for compact output).

## MCP tool error codes

All four MCP tools return the same `{ok, data|error}` envelope. The `code` field is stable across versions; the `message` field is human-readable and may change.

| Code                   | Meaning |
| ---------------------- | ------- |
| `URL_TOO_LONG`         | Submitted URL exceeds the 2048-character limit. |
| `URL_NOT_ALLOWED`      | URL failed SSRF validation (private, loopback, link-local, or reserved address). |
| `INVALID_PRESET`       | `preset` value is not one of the seven supported preset identifiers. |
| `RATE_LIMIT_EXCEEDED`  | Fixed-window limit of 3/minute per client was exceeded. |
| `AUDIT_SETUP_FAILED`   | The server could not build the audit context for the submitted URL. |
| `AUDIT_NOT_FOUND`      | The supplied `audit_id` is not in the in-process LRU cache. |
| `CHECK_NOT_FOUND`      | The supplied `check_name` is not one of the 13 Essentials check identifiers. |

Example error envelope:

```json
{
  "ok": false,
  "error": { "code": "RATE_LIMIT_EXCEEDED", "message": "Rate limit exceeded. Retry after the fixed window resets." }
}
```

## Namespace mapping

| Outcome                 | HTTP status | Helper exit | MCP code               |
| ----------------------- | ----------- | ----------- | ---------------------- |
| Success                 | `200`       | `0`         | (no error; `ok: true`) |
| Bad request body        | `422`       | `1`         | (no MCP equivalent)    |
| URL resolves to private | `400`       | `1`         | `URL_NOT_ALLOWED`      |
| URL too long            | `422`       | `2`         | `URL_TOO_LONG`         |
| Unknown preset          | `422`       | `1`         | `INVALID_PRESET`       |
| Rate limit              | `429`       | `3`         | `RATE_LIMIT_EXCEEDED`  |
| Server setup failure    | `500`       | `4`         | `AUDIT_SETUP_FAILED`   |
| Cache miss              | n/a         | n/a         | `AUDIT_NOT_FOUND`      |
| Unknown check name      | n/a         | n/a         | `CHECK_NOT_FOUND`      |
| Connection error        | n/a         | `5`         | (no MCP equivalent)    |
| Bad CLI args            | n/a         | `2`         | (no MCP equivalent)    |

When the helper script translates an HTTP `422` to exit code `1`, the original `detail` and `errors` fields are written to stderr so the caller can still see the structured validation failure.
