"""Refresh MachineRead benchmark profiles from a peers list.

Stdlib-only (no third-party deps). Calls the canonical audit pipeline
without spinning up an HTTP server. Emits a v2 profile JSON file.

Usage:
  python scripts/refresh_benchmarks.py --peers scripts/benchmark_peers.sample.json
                                       --out backend/private_data/benchmark_profiles.json
                                       --concurrency 2

A failed peer is OMITTED from the output (not zero-filled), and a summary
is printed at the end. The script never blocks the user.
"""
import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

# Add backend to path so we can import the app
HERE = Path(__file__).resolve()
BACKEND_DIR = HERE.parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.main import _execute_audit  # noqa: E402  -- import after sys.path mutation
from app.models import AuditRequest  # noqa: E402


SCHEMA_VERSION = 2


async def _audit_one(peer: dict) -> dict:
    """Run the canonical audit at full scope and return a v2 profile dict.

    ``_execute_audit`` is the same async function the FastAPI route
    ``POST /v1/audit`` invokes; calling it directly skips the HTTP layer
    but exercises the canonical pipeline (URL validation, scope
    resolution, essential checks, agent readiness).
    """
    request = AuditRequest(
        url=peer["url"],
        preset="custom",
        # Custom/Power User preset only accepts short override keys
        # (no ``include_`` prefix). See backend/app/presets.py
        # ``_VALID_OVERRIDE_KEYS`` and AuditRequest.custom_overrides in
        # backend/app/models.py.
        #
        # Full scope = all three secondary dimensions plus every sub-family.
        # The coherence check at backend/app/presets.py rejects a request
        # that enables a top-level dimension (protocols / account_auth /
        # ecommerce) without at least one matching sub-family. The captured
        # profile stores whatever this full-scope audit produces; peer_agent
        # later counts probes via _scope_probe_labels, which already
        # reflects the user's actual chosen scope.
        custom_overrides={
            # Top-level secondary dimensions
            "protocols": True,
            "account_auth": True,
            "ecommerce": True,
            # Protocol sub-families
            "api_catalog": True,
            "mcp": True,
            "a2a": True,
            "agent_skills": True,
            "webmcp": True,
            # Account-auth sub-families
            "oauth_oidc": True,
            "auth_md": True,
            # Ecommerce sub-families
            "product_offer_schema": True,
            "commerce_fields": True,
        },
    )
    result = await _execute_audit(request)
    return _to_profile(peer, result)


def _to_profile(peer: dict, result: Any) -> dict:
    """Project an AuditResult into the v2 profile shape."""
    essentials_checks: dict[str, dict[str, Any]] = {}
    for check in result.checks:
        # Locked rows are reserved for the paid tier and never contribute;
        # skip them so they do not inflate checked_max at query time.
        if check.state == "locked":
            continue
        essentials_checks[check.check_name] = {
            "score": int(check.score),
            "max": int(check.max_score),
            "state": check.state,
            "evidence_level": check.evidence_level,
        }
    return {
        "name": peer["name"],
        "category": peer["category"],
        "group": peer["group"],
        "size": peer["size"],
        "url": peer["url"],
        "essentials_checks": essentials_checks,
        # Top-level passed list (not the per-category passed lists)
        "agent_passed": list(result.agent_readiness.passed),
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Refresh MachineRead benchmark profiles")
    p.add_argument("--peers", type=Path, required=True, help="JSON list of peer dicts")
    p.add_argument(
        "--out",
        type=Path,
        default=Path("backend/private_data/benchmark_profiles.json"),
        help="Output v2 profile file",
    )
    p.add_argument("--concurrency", type=int, default=2, help="Max concurrent audits")
    return p.parse_args()


async def _run(args: argparse.Namespace) -> int:
    peers = json.loads(args.peers.read_text(encoding="utf-8"))
    if not isinstance(peers, list):
        print(f"ERROR: {args.peers} must be a JSON list of peer dicts", file=sys.stderr)
        return 1
    sem = asyncio.Semaphore(args.concurrency)
    profiles: list[dict] = []
    failures: list[tuple[str, str]] = []

    async def one(peer: dict) -> None:
        async with sem:
            try:
                profile = await _audit_one(peer)
                profiles.append(profile)
                print(f"  OK   {peer['name']}")
            except Exception as exc:
                failures.append((peer["name"], repr(exc)))
                print(f"  FAIL {peer['name']}: {exc!r}")

    await asyncio.gather(*[one(p) for p in peers])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_date": str(date.today()),
        "profiles": profiles,
    }
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print()
    print(f"Wrote {len(profiles)} profile(s) to {args.out}")
    if failures:
        print(f"{len(failures)} peer(s) failed and were omitted:")
        for name, err in failures:
            print(f"  - {name}: {err}")
        return 2  # nonzero so callers know some peers failed
    return 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
