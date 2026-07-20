#!/usr/bin/env python3
"""Run a free MachineRead Essentials audit from the command line.

Public helper for the machineread-audit Agent Skill package.
Calls POST /v1/audit. Stdlib only.

Usage:
    python scripts/run_audit.py https://example.com/
    python scripts/run_audit.py https://example.com/ --preset saas
    python scripts/run_audit.py https://example.com/ --field overall_score
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_API = "https://api.machineread.ai"
TIMEOUT_DEFAULT = 30
TIMEOUT_MIN = 5
TIMEOUT_MAX = 60
MAX_URL_LENGTH = 2048
ALLOWED_SCHEMES = ("http", "https")
LOOPBACK_HOSTS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "[::1]",
        "0:0:0:0:0:0:0:1",
    }
)


def _api_base() -> str:
    """Return the API base URL (env override, no trailing slash)."""
    import os

    raw = os.environ.get("MACHINEREAD_API") or DEFAULT_API
    return raw.rstrip("/")


def _validate_url(url: str) -> str:
    """Apply cheap local validation. Returns the URL or raises ValueError."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL is required")
    candidate = url.strip()
    if len(candidate) > MAX_URL_LENGTH:
        raise ValueError(f"URL exceeds {MAX_URL_LENGTH}-character limit")
    parsed = urllib.parse.urlparse(candidate)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"URL scheme must be one of {ALLOWED_SCHEMES}, got {scheme!r}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL is missing a host")
    if host in LOOPBACK_HOSTS:
        raise ValueError(f"URL resolves to a loopback host: {host}")
    return candidate


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_audit",
        description="Run a free MachineRead Essentials audit from the command line.",
    )
    parser.add_argument("url", help="Public HTTP(S) URL to audit.")
    parser.add_argument(
        "--preset",
        choices=["blog", "corporate", "services", "ecommerce", "news", "saas", "custom"],
        default=None,
        help="Optional scope selector (default: legacy boolean path).",
    )
    parser.add_argument(
        "--custom-overrides",
        default=None,
        help='JSON object string applied on top of preset (requires --preset).',
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=TIMEOUT_DEFAULT,
        help=f"Request timeout in seconds (default {TIMEOUT_DEFAULT}, clamped {TIMEOUT_MIN}-{TIMEOUT_MAX}).",
    )
    parser.add_argument(
        "--field",
        default=None,
        help="Dotted path to extract a single value (e.g. overall_score, agent_readiness.score).",
    )
    parser.add_argument(
        "--no-pretty",
        action="store_true",
        help="Emit compact JSON instead of pretty-printed output.",
    )
    return parser


def _cli_usage_error(message: str) -> int:
    """Emit a CLI usage error to stderr and return exit code 2."""
    print(f"run_audit: {message}", file=sys.stderr)
    return 2


def _parse_overrides(raw: str | None) -> dict[str, Any] | None:
    """Parse the --custom-overrides JSON string into a dict."""
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--custom-overrides is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("--custom-overrides must decode to a JSON object")
    return parsed


def _resolve_field(payload: Any, dotted: str) -> Any:
    """Walk a dotted path; return None if any segment is missing."""
    current: Any = payload
    for segment in dotted.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            return None
    return current


def _emit(payload: Any, *, pretty: bool, field: str | None) -> None:
    """Write the result to stdout."""
    if field:
        value = _resolve_field(payload, field)
        if value is None:
            print("null")
        elif isinstance(value, (dict, list)):
            indent = None if not pretty else 2
            print(json.dumps(value, indent=indent, sort_keys=not pretty))
        else:
            print(value)
        return
    indent = None if not pretty else 2
    print(json.dumps(payload, indent=indent, sort_keys=not pretty))


def _request(url: str, body: dict[str, Any], timeout: int) -> dict[str, Any]:
    """POST the audit request and return the parsed JSON payload or raise."""
    request = urllib.request.Request(
        url=url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 0 for --help, 2 for parse errors
        return exc.code if exc.code in (0, 2) else 2

    try:
        url = _validate_url(args.url)
    except ValueError as exc:
        return _cli_usage_error(str(exc))

    try:
        overrides = _parse_overrides(args.custom_overrides)
    except ValueError as exc:
        return _cli_usage_error(str(exc))

    if overrides is not None and not args.preset:
        return _cli_usage_error("--custom-overrides requires --preset")

    timeout = max(TIMEOUT_MIN, min(TIMEOUT_MAX, int(args.timeout)))

    body: dict[str, Any] = {"url": url}
    if args.preset:
        body["preset"] = args.preset
    if overrides:
        body["custom_overrides"] = overrides

    request_url = f"{_api_base()}/v1/audit"
    try:
        payload = _request(request_url, body, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
            parsed = json.loads(error_body)
            detail = parsed.get("detail") or parsed.get("message") or ""
        except Exception:  # noqa: BLE001 - best-effort diagnostics
            detail = exc.reason if hasattr(exc, "reason") else str(exc)
        print(
            f"run_audit: HTTP {exc.code}: {detail or exc.reason}",
            file=sys.stderr,
        )
        if exc.code == 429:
            return 3
        if 400 <= exc.code < 500:
            return 1
        if 500 <= exc.code < 600:
            return 4
        return 1
    except urllib.error.URLError as exc:
        print(f"run_audit: connection error: {exc.reason}", file=sys.stderr)
        return 5
    except (TimeoutError, ConnectionError, OSError) as exc:
        print(f"run_audit: network error: {exc}", file=sys.stderr)
        return 5
    except json.JSONDecodeError as exc:
        print(f"run_audit: response was not valid JSON: {exc}", file=sys.stderr)
        return 4

    pretty = not args.no_pretty
    _emit(payload, pretty=pretty, field=args.field)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
