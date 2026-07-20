"""Pure bounded validation for ARD static catalog payloads."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

_MAX_PAYLOAD_BYTES = 500 * 1024
_SEMVER_PATTERN = re.compile(r"^\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.-]+)?$")
_URN_AIR_PATTERN = re.compile(r"^urn:air:[a-z0-9.-]+(?::[a-z0-9._/-]+)+$", re.IGNORECASE)
_HTTP_SCHEMES = {"http", "https"}


def _looks_like_semver(value: str) -> bool:
    return bool(_SEMVER_PATTERN.fullmatch(value.strip()))


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _domain_matches_audit(value: Any, audit_domain: str) -> bool:
    if not _non_empty_string(value) or not audit_domain:
        return False
    candidate = value.strip().lower().rstrip(".")
    parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    normalized_audit_domain = audit_domain.lower().removeprefix("www.")
    normalized_hostname = hostname.removeprefix("www.")
    return normalized_hostname == normalized_audit_domain or normalized_hostname.endswith(
        f".{normalized_audit_domain}"
    )


def _entry_identifier(entry: dict[str, Any]) -> Any:
    return entry.get("identifier") or entry.get("urn") or entry.get("id")


def _is_domain_anchored_urn(value: Any, audit_domain: str) -> bool:
    if not _non_empty_string(value):
        return False
    identifier = value.strip()
    if not _URN_AIR_PATTERN.fullmatch(identifier):
        return False
    if not audit_domain:
        return True
    publisher = identifier.split(":", 3)[2].lower().removeprefix("www.")
    normalized_audit_domain = audit_domain.lower().removeprefix("www.")
    return publisher == normalized_audit_domain or publisher.endswith(f".{normalized_audit_domain}")


def _is_timestamp(value: Any) -> bool:
    if not _non_empty_string(value):
        return False
    candidate = value.strip()
    try:
        datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _has_capability_metadata(entry: dict[str, Any]) -> bool:
    for key in ("capabilities", "tags"):
        value = entry.get(key)
        if isinstance(value, list) and any(_non_empty_string(item) for item in value):
            return True
    return False


def _check_entry(entry: Any, audit_domain: str) -> list[str]:
    if not isinstance(entry, dict):
        return ["entry is not a JSON object"]

    issues: list[str] = []
    identifier = _entry_identifier(entry)
    if not _non_empty_string(identifier):
        issues.append("entry is missing `identifier`")
    elif not _is_domain_anchored_urn(identifier, audit_domain):
        issues.append(f"entry identifier `{identifier}` is not a domain-anchored `urn:air` value")

    if not _non_empty_string(entry.get("displayName")):
        issues.append("entry is missing `displayName`")
    if not _non_empty_string(entry.get("mediaType") or entry.get("type")):
        issues.append("entry is missing `mediaType`")

    has_url = "url" in entry
    has_data = "data" in entry
    if has_url == has_data:
        issues.append("entry must declare exactly one of `url` or `data`")
    elif has_url:
        if not _non_empty_string(entry.get("url")):
            issues.append("entry `url` must be a non-empty HTTP(S) URL")
        else:
            parsed_url = urlparse(entry["url"].strip())
            if parsed_url.scheme.lower() not in _HTTP_SCHEMES or not parsed_url.netloc:
                issues.append("entry `url` must be an absolute HTTP(S) URL")
    elif not isinstance(entry.get("data"), dict):
        issues.append("entry `data` must be an embedded JSON object")

    if not _non_empty_string(entry.get("description")):
        issues.append("entry is missing `description`")
    if not _has_capability_metadata(entry):
        issues.append("entry is missing non-empty `capabilities` or `tags`")

    queries = entry.get("representativeQueries")
    if not isinstance(queries, list) or not queries or not all(_non_empty_string(query) for query in queries):
        issues.append("entry is missing non-empty `representativeQueries`")

    updated = entry.get("updated") or entry.get("updatedAt") or entry.get("lastUpdated")
    if not _is_timestamp(updated):
        issues.append("entry is missing an ISO 8601 `updated` timestamp")

    return issues


def _result(
    valid: bool,
    spec_version: str | None,
    entry_count: int,
    has_trust_manifest: bool,
    issues: list[str],
) -> dict[str, Any]:
    return {
        "valid": valid,
        "spec_version": spec_version,
        "entry_count": entry_count,
        "has_trust_manifest": has_trust_manifest,
        "issues": issues,
        "sample_issues": issues[:5],
    }


def ard_catalog_quality(text: str, audit_url: str | None = None) -> dict[str, Any]:
    """Validate an ARD catalog body without network or provider calls."""
    if not isinstance(text, str):
        return _result(False, None, 0, False, ["ARD catalog body is not text"])

    payload_size = len(text.encode("utf-8"))
    if payload_size > _MAX_PAYLOAD_BYTES:
        return _result(
            False,
            None,
            0,
            False,
            [f"ARD catalog payload exceeds the 500 KiB limit ({payload_size} bytes)"],
        )
    if not text.strip():
        return _result(False, None, 0, False, ["ARD catalog body is empty"])

    try:
        catalog = json.loads(text)
    except json.JSONDecodeError as exc:
        return _result(False, None, 0, False, [f"ARD catalog is not valid JSON: {exc.msg}"])

    if not isinstance(catalog, dict):
        return _result(False, None, 0, False, ["ARD catalog root must be a JSON object"])

    issues: list[str] = []
    spec_version = catalog.get("specVersion")
    if not _non_empty_string(spec_version):
        issues.append("ARD catalog is missing `specVersion`")
        spec_version_value = None
    else:
        spec_version_value = spec_version.strip()
        if not _looks_like_semver(spec_version_value):
            issues.append(f"ARD catalog `specVersion` `{spec_version_value}` is not semver-shaped")

    audit_domain = ""
    if audit_url:
        audit_domain = (urlparse(audit_url).hostname or "").strip(".").lower()

    host = catalog.get("host")
    if not isinstance(host, dict):
        issues.append("ARD catalog is missing `host` metadata")
    else:
        host_domain = host.get("domain") or host.get("hostname") or host.get("url")
        if host_domain is None:
            issues.append("ARD catalog host metadata is missing `domain`")
        elif audit_domain and not _domain_matches_audit(host_domain, audit_domain):
            issues.append(f"ARD catalog host domain `{host_domain}` does not match the audited host")
        elif not audit_domain and not _non_empty_string(host_domain):
            issues.append("ARD catalog host `domain` must be non-empty")

    entries = catalog.get("entries")
    if not isinstance(entries, list):
        issues.append("ARD catalog is missing an `entries` list")
        entry_count = 0
    else:
        entry_count = len(entries)
        for index, entry in enumerate(entries):
            issues.extend(f"entry[{index}]: {issue}" for issue in _check_entry(entry, audit_domain))

    has_trust_manifest = "trustManifest" in catalog or (
        isinstance(host, dict) and "trustManifest" in host
    ) or any(isinstance(entry, dict) and "trustManifest" in entry for entry in entries or [])

    return _result(not issues, spec_version_value, entry_count, has_trust_manifest, issues)


__all__ = ["ard_catalog_quality"]
