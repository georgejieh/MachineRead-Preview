"""Per-check benchmark peer profiles (schema v2).

Peer profiles are stored per-check rather than as pre-baked per-scope
variants. A single peer record captures the results of one full-scope
audit run (essentials_checks for the 13 included groups plus the
agent_passed probe labels). Peer scores are then recomputed at query
time from the user's chosen scope:

- ``peer_essentials`` sums score/max over the 13 groups, applying the
  SAME exclusion rule as ``_checked_points`` (rows with
  ``state == "locked"`` OR ``evidence_level == "unknown"`` contribute
  to neither numerator nor denominator).
- ``peer_agent`` counts how many of the passed labels match the
  current scope's probe list (``_scope_probe_labels``). Labels in
  ``agent_passed`` that are no longer present in the current probe
  list are silently ignored, so profile snapshots survive probe-list
  evolution.

The public tree ships a hand-curated ``_SAMPLE_BENCHMARK_SEEDS`` for
the demo experience. The private deploy refreshes the on-disk snapshot
monthly via ``scripts/refresh_benchmarks.py``.
"""
import json
import os
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from app.models import (
    AgentBenchmarkComparison,
    BenchmarkComparison,
    BenchmarkEntry,
    CheckResult,
    PillarScores,
)
from app.rubric import ESSENTIALS_CHECKED_MAX, ESSENTIALS_CHECK_GROUPS

# ``_scope_probe_labels`` is imported lazily inside ``peer_agent`` to break
# the import cycle: ``app.agent_readiness`` imports ``build_agent_benchmark_comparison``
# from this module, so a top-level import here would deadlock on the
# partially-initialized module.

SCHEMA_VERSION = 2
BENCHMARK_SNAPSHOT_DATE = "2026-07-17"  # fallback only when no file is loaded
BENCHMARK_BASIS = "Essentials evidence score from the MachineRead audit"
AGENT_BENCHMARK_BASIS = "Agent-native readiness score from the MachineRead audit"
BENCHMARK_CAVEAT = (
    "Benchmark peers are scored under the same selected Essentials scope using bounded "
    "public HTTP, DNS, and page evidence. Comparable scores do not imply comparable AI "
    "exposure, search traffic, ecommerce conversion, ranking strength, backlinks, social "
    "traction, real Google/Bing/Brave index coverage, Core Web Vitals field data, or live "
    "model citation share. "
    f"The current Essentials denominator is {ESSENTIALS_CHECKED_MAX} checked points across "
    f"{len(ESSENTIALS_CHECK_GROUPS)} included check groups; each group may aggregate "
    "several underlying signals. Review agent-native readiness separately for explicit "
    "agent discovery."
)
AGENT_BENCHMARK_CAVEAT = (
    "This benchmark compares explicit agent-native discovery and protocol signals under "
    "the same selected audit scope. Low scores are common among current large sites, but "
    "that should be read as market immaturity rather than evidence that missing surfaces "
    "are irrelevant. Stored profile earned counts are normalized to the current scope "
    "denominator when the strict probe list changes."
)

BenchmarkProfile = dict[str, Any]


@dataclass(frozen=True)
class _CheckSnapshot:
    """Immutable per-check snapshot stored in a loaded v2 profile.

    The on-disk JSON keeps keys ``score``, ``max``, ``state``, and
    ``evidence_level`` (per the public schema); after validation we
    freeze each entry into this dataclass so the runtime never
    mutates the snapshot.
    """

    score: int
    max_score: int
    state: str
    evidence_level: str


_DEFAULT_BENCHMARK_PROFILE_PATH = (
    Path(__file__).resolve().parents[1] / "private_data" / "benchmark_profiles.json"
)


# --- Validation (eager, at load time) -------------------------------------

def _validate_profile(
    profile: dict,
    valid_check_names: set[str],
    rubric_max: dict[str, int],
) -> None:
    """Raise ValueError if profile is malformed. Failure is loud at startup."""
    for key in ("name", "category", "group", "size", "url", "essentials_checks", "agent_passed"):
        if key not in profile:
            raise ValueError(f"profile missing required key {key!r}")
    for key in ("name", "category", "group", "size", "url"):
        if not isinstance(profile[key], str):
            raise ValueError(
                f"profile {profile.get('name')!r}: {key!r} must be a string, "
                f"got {type(profile[key]).__name__}"
            )
    checks = profile["essentials_checks"]
    if not isinstance(checks, dict):
        raise ValueError(f"profile {profile['name']!r}: essentials_checks must be a dict")
    actual = set(checks.keys())
    if actual != valid_check_names:
        missing = valid_check_names - actual
        extra = actual - valid_check_names
        problems = []
        if missing:
            problems.append(f"missing {sorted(missing)}")
        if extra:
            problems.append(f"unknown {sorted(extra)}")
        raise ValueError(
            f"profile {profile['name']!r}: essentials_checks keys: {'; '.join(problems)}"
        )
    for check_name, entry in checks.items():
        if not isinstance(entry, dict):
            raise ValueError(
                f"profile {profile['name']!r}: essentials_checks[{check_name!r}] must be a dict"
            )
        for key in ("score", "max", "state", "evidence_level"):
            if key not in entry:
                raise ValueError(
                    f"profile {profile['name']!r}: essentials_checks[{check_name!r}] "
                    f"missing {key!r}"
                )
        if not isinstance(entry["score"], int) or isinstance(entry["score"], bool):
            raise ValueError(
                f"profile {profile['name']!r}: essentials_checks[{check_name!r}].score "
                f"must be an int"
            )
        if not isinstance(entry["max"], int) or isinstance(entry["max"], bool):
            raise ValueError(
                f"profile {profile['name']!r}: essentials_checks[{check_name!r}].max "
                f"must be an int"
            )
        if entry["max"] != rubric_max[check_name]:
            raise ValueError(
                f"profile {profile['name']!r}: essentials_checks[{check_name!r}].max "
                f"is {entry['max']}, rubric says {rubric_max[check_name]}"
            )
        if not (0 <= entry["score"] <= entry["max"]):
            raise ValueError(
                f"profile {profile['name']!r}: essentials_checks[{check_name!r}].score "
                f"{entry['score']} not in [0, {entry['max']}]"
            )
        if entry["state"] not in ("pass", "partial", "fail", "warn", "locked"):
            raise ValueError(
                f"profile {profile['name']!r}: essentials_checks[{check_name!r}].state "
                f"{entry['state']!r} unknown"
            )
        if entry["evidence_level"] not in ("verified", "inferred", "unknown"):
            raise ValueError(
                f"profile {profile['name']!r}: essentials_checks[{check_name!r}].evidence_level "
                f"{entry['evidence_level']!r} unknown"
            )
    agent_passed = profile["agent_passed"]
    if not isinstance(agent_passed, list):
        raise ValueError(f"profile {profile['name']!r}: agent_passed must be a list")
    for i, label in enumerate(agent_passed):
        if not isinstance(label, str):
            raise ValueError(
                f"profile {profile['name']!r}: agent_passed[{i}] must be a string, "
                f"got {type(label).__name__}"
            )


def _coerce_entry(entry: Any) -> _CheckSnapshot:
    """Accept either a loaded ``_CheckSnapshot`` or a raw dict with
    ``score``/``max``/``state``/``evidence_level`` keys. Used by
    ``_check_payload`` so that ad-hoc test profiles (dict shape) and
    loaded profiles (frozen shape) both work.
    """
    if isinstance(entry, _CheckSnapshot):
        return entry
    return _CheckSnapshot(
        score=int(entry["score"]),
        max_score=int(entry["max"]),
        state=entry["state"],
        evidence_level=entry["evidence_level"],
    )


def _check_payload(profile: dict) -> list[CheckResult]:
    """Project a v2 profile's essentials_checks into CheckResult-like rows so
    peer_essentials can reuse the existing _checked_points rule.
    """
    pillar_by_check = {g.check_name: g.pillar for g in ESSENTIALS_CHECK_GROUPS}
    label_by_check = {g.check_name: g.label for g in ESSENTIALS_CHECK_GROUPS}
    rows = []
    for check_name, entry in profile["essentials_checks"].items():
        snap = _coerce_entry(entry)
        rows.append(
            CheckResult(
                pillar=pillar_by_check[check_name],
                check_name=check_name,
                label=label_by_check[check_name],
                state=snap.state,
                score=snap.score,
                max_score=snap.max_score,
                finding="",
                fix="",
                effort="low",
                evidence_level=snap.evidence_level,
            )
        )
    return rows


def peer_essentials(profile: dict) -> tuple[int, int, PillarScores]:
    """Sum score/max over the 13 groups, applying the SAME exclusion rule
    as ``_checked_points()`` (rows with ``state == 'locked'`` OR
    ``evidence_level == 'unknown'`` contribute to neither numerator nor
    denominator). Returns ``(checked_score, checked_max, pillar_scores)``.
    """
    rows = _check_payload(profile)
    checked_score, checked_max = _checked_points(rows)
    by_pillar = {"off_site": 0, "scrapability": 0, "seo": 0}
    pillar_by_check = {g.check_name: g.pillar for g in ESSENTIALS_CHECK_GROUPS}
    for row in rows:
        if row.state == "locked" or row.evidence_level == "unknown":
            continue
        by_pillar[pillar_by_check[row.check_name]] += row.score
    return checked_score, checked_max, PillarScores(**by_pillar)


def peer_agent(
    profile: dict,
    include_protocols: bool,
    include_account_auth: bool,
    include_ecommerce: bool,
) -> tuple[int, int]:
    """Count how many probes the peer passed in the current scope.
    Labels in ``agent_passed`` that no longer exist in
    ``_scope_probe_labels`` are silently ignored (robust to probe-list
    evolution).
    """
    from app.agent_readiness import _scope_probe_labels  # lazy: break import cycle

    labels = _scope_probe_labels(include_protocols, include_account_auth, include_ecommerce)
    passed = set(profile["agent_passed"])
    earned = sum(1 for label in labels if label in passed)
    return earned, len(labels)


# --- _agent_max_for_scope (KEEP — used by tests) ---------------------------

def _agent_max_for_scope(
    include_protocols: bool = False,
    include_account_auth: bool = False,
    include_ecommerce: bool = False,
) -> int:
    return 8 + (6 if include_protocols else 0) + (3 if include_account_auth else 0) + (
        4 if include_ecommerce else 0
    )


# --- Scope-key helper (KEEP for legacy tests + label display) --------------

def benchmark_scope_key(
    include_protocols: bool = False,
    include_account_auth: bool = False,
    include_ecommerce: bool = False,
) -> str:
    """Label-only display helper. v2 stores one profile per peer; this key
    is preserved for tests and UI display that asserts on the variant
    shape. It is no longer used by the benchmarks comparison path.
    """
    return f"p{int(include_protocols)}_a{int(include_account_auth)}_c{int(include_ecommerce)}"


# --- Sample seeds (12 fictional peers, v2 profile dicts) ------------------

# Each peer's per-check scores were chosen so the sum over kept rows equals
# the v1 checked_score for the corresponding peer. agent_passed labels were
# distributed so commerce/API peers include protocol-scope probes while
# corporate/editorial peers do not.

_BASE_CORE_LABELS = (
    "robots.txt published",
    "valid sitemap discovery",
    "agent discovery Link headers",
    "DNS-AID records",
    "llms.txt or Markdown negotiation",
    "AI-specific robots.txt rules",
    "Content Signals in robots.txt",
)
_PROTOCOL_LABELS = (
    "API Catalog",
    "MCP Server Card",
    "A2A Agent Card",
    "Agent Skills index",
    "WebMCP manifest",
    "ARD static catalog",
)
_AUTH_LABEL = ("auth.md",)


def _seed_profile(
    *,
    name: str,
    category: str,
    group: str,
    size: str,
    url: str,
    check_scores: dict[str, int],
    agent_passed: list[str],
) -> dict[str, Any]:
    """Build a v2 profile dict from per-check score map and passed label list."""
    by_check = {g.check_name: g for g in ESSENTIALS_CHECK_GROUPS}
    essentials_checks: dict[str, dict[str, Any]] = {}
    for check_name, group_obj in by_check.items():
        max_score = group_obj.max_score
        score = check_scores[check_name]
        if score == max_score:
            state = "pass"
        elif score > 0:
            state = "partial"
        else:
            state = "fail"
        essentials_checks[check_name] = {
            "score": score,
            "max": max_score,
            "state": state,
            "evidence_level": "verified",
        }
    return {
        "name": name,
        "category": category,
        "group": group,
        "size": size,
        "url": url,
        "essentials_checks": essentials_checks,
        "agent_passed": list(agent_passed),
    }


def _commerce_passed(base_earned: int, *, with_protocols: bool = False) -> list[str]:
    """Return a plausible agent_passed list for a commerce-style peer."""
    passed = list(_BASE_CORE_LABELS[: min(len(_BASE_CORE_LABELS), base_earned)])
    if with_protocols and base_earned > len(_BASE_CORE_LABELS):
        slots = base_earned - len(_BASE_CORE_LABELS)
        passed.extend(_PROTOCOL_LABELS[:slots])
    return passed


def _editorial_passed(base_earned: int) -> list[str]:
    """Return a plausible agent_passed list for an editorial/corporate peer."""
    return list(_BASE_CORE_LABELS[: min(len(_BASE_CORE_LABELS), base_earned)])


# v1 totals per peer (off_site, scrapability, seo) -> rebuilt here as per-check
# distributions that sum to the same totals. Sample-only; the private deploy
# regenerates real data via scripts/refresh_benchmarks.py.
_SAMPLE_BENCHMARK_SEEDS: list[dict] = [
    # Commerce enterprise, strong scrapability
    _seed_profile(
        name="Example Retail Enterprise",
        category="Major retailer",
        group="commerce",
        size="enterprise",
        url="https://example.com/retail",
        check_scores={
            "social": 1, "wikipedia": 4,
            "robots_txt": 6, "bot_access": 6, "html_structure": 4,
            "schema_ld": 5, "llms_txt": 0, "ssr": 2, "machine_surfaces": 2,
            "pagespeed": 2, "canonical": 5, "indexing": 4, "search_discovery": 3,
        },
        agent_passed=_commerce_passed(3, with_protocols=False),
    ),
    # Commerce enterprise, balanced
    _seed_profile(
        name="Example Consumer Brand",
        category="Major consumer brand",
        group="commerce",
        size="enterprise",
        url="https://example.com/consumer",
        check_scores={
            "social": 2, "wikipedia": 3,
            "robots_txt": 5, "bot_access": 5, "html_structure": 3,
            "schema_ld": 4, "llms_txt": 0, "ssr": 3, "machine_surfaces": 2,
            "pagespeed": 2, "canonical": 5, "indexing": 4, "search_discovery": 3,
        },
        agent_passed=_commerce_passed(2, with_protocols=False),
    ),
    # Commerce enterprise, strong on protocol surfaces (API catalog etc.)
    _seed_profile(
        name="Example Commerce Platform",
        category="Commerce platform",
        group="commerce",
        size="enterprise",
        url="https://example.com/platform",
        check_scores={
            "social": 2, "wikipedia": 4,
            "robots_txt": 6, "bot_access": 6, "html_structure": 4,
            "schema_ld": 5, "llms_txt": 0, "ssr": 3, "machine_surfaces": 2,
            "pagespeed": 3, "canonical": 5, "indexing": 5, "search_discovery": 3,
        },
        agent_passed=_commerce_passed(5, with_protocols=True),
    ),
    # Specialty DTC product, mid-tier
    _seed_profile(
        name="Example Product Studio",
        category="Niche DTC product",
        group="commerce",
        size="specialty",
        url="https://example.com/product",
        check_scores={
            "social": 1, "wikipedia": 3,
            "robots_txt": 5, "bot_access": 5, "html_structure": 4,
            "schema_ld": 4, "llms_txt": 0, "ssr": 4, "machine_surfaces": 2,
            "pagespeed": 3, "canonical": 5, "indexing": 4, "search_discovery": 3,
        },
        agent_passed=_commerce_passed(3, with_protocols=False),
    ),
    # Niche beauty brand, mid-tier
    _seed_profile(
        name="Example Beauty Brand",
        category="Niche beauty brand",
        group="commerce",
        size="specialty",
        url="https://example.com/beauty",
        check_scores={
            "social": 1, "wikipedia": 3,
            "robots_txt": 5, "bot_access": 5, "html_structure": 4,
            "schema_ld": 4, "llms_txt": 0, "ssr": 4, "machine_surfaces": 2,
            "pagespeed": 3, "canonical": 5, "indexing": 4, "search_discovery": 2,
        },
        agent_passed=_commerce_passed(3, with_protocols=False),
    ),
    # Specialty commerce, weak
    _seed_profile(
        name="Example Outdoor Shop",
        category="Specialty commerce",
        group="commerce",
        size="specialty",
        url="https://example.com/outdoor",
        check_scores={
            "social": 1, "wikipedia": 1,
            "robots_txt": 3, "bot_access": 3, "html_structure": 2,
            "schema_ld": 2, "llms_txt": 0, "ssr": 2, "machine_surfaces": 2,
            "pagespeed": 1, "canonical": 3, "indexing": 2, "search_discovery": 2,
        },
        agent_passed=_commerce_passed(1, with_protocols=False),
    ),
    # Boutique commerce, strong SEO
    _seed_profile(
        name="Example Paper Goods",
        category="Boutique product",
        group="commerce",
        size="boutique",
        url="https://example.com/paper",
        check_scores={
            "social": 2, "wikipedia": 2,
            "robots_txt": 4, "bot_access": 4, "html_structure": 2,
            "schema_ld": 3, "llms_txt": 0, "ssr": 3, "machine_surfaces": 2,
            "pagespeed": 3, "canonical": 5, "indexing": 5, "search_discovery": 3,
        },
        agent_passed=_commerce_passed(2, with_protocols=False),
    ),
    # Boutique commerce, weak
    _seed_profile(
        name="Example Desk Goods",
        category="Boutique product",
        group="commerce",
        size="boutique",
        url="https://example.com/desk",
        check_scores={
            "social": 1, "wikipedia": 1,
            "robots_txt": 2, "bot_access": 2, "html_structure": 1,
            "schema_ld": 1, "llms_txt": 0, "ssr": 1, "machine_surfaces": 1,
            "pagespeed": 1, "canonical": 2, "indexing": 1, "search_discovery": 0,
        },
        agent_passed=_commerce_passed(1, with_protocols=False),
    ),
    # Major technology brand (corporate), mid-tier
    _seed_profile(
        name="Example Technology Company",
        category="Major technology brand",
        group="corporate",
        size="enterprise",
        url="https://example.com/technology",
        check_scores={
            "social": 1, "wikipedia": 3,
            "robots_txt": 6, "bot_access": 6, "html_structure": 4,
            "schema_ld": 4, "llms_txt": 0, "ssr": 3, "machine_surfaces": 2,
            "pagespeed": 2, "canonical": 5, "indexing": 4, "search_discovery": 3,
        },
        agent_passed=_editorial_passed(3),
    ),
    # Developer platform, strong on protocols
    _seed_profile(
        name="Example Developer Platform",
        category="Developer platform",
        group="corporate",
        size="specialty",
        url="https://example.com/developer",
        check_scores={
            "social": 2, "wikipedia": 4,
            "robots_txt": 6, "bot_access": 6, "html_structure": 4,
            "schema_ld": 5, "llms_txt": 0, "ssr": 3, "machine_surfaces": 2,
            "pagespeed": 3, "canonical": 5, "indexing": 5, "search_discovery": 3,
        },
        agent_passed=_BASE_CORE_LABELS + _PROTOCOL_LABELS,
    ),
    # Software company (boutique), mid-tier
    _seed_profile(
        name="Example Software Studio",
        category="Software company",
        group="corporate",
        size="boutique",
        url="https://example.com/software",
        check_scores={
            "social": 2, "wikipedia": 2,
            "robots_txt": 5, "bot_access": 5, "html_structure": 3,
            "schema_ld": 3, "llms_txt": 0, "ssr": 3, "machine_surfaces": 2,
            "pagespeed": 2, "canonical": 3, "indexing": 4, "search_discovery": 2,
        },
        agent_passed=_editorial_passed(2),
    ),
    # Professional services / SaaS, strong on protocols
    _seed_profile(
        name="Example Scheduling Service",
        category="SaaS service",
        group="service",
        size="specialty",
        url="https://example.com/scheduling",
        check_scores={
            "social": 2, "wikipedia": 4,
            "robots_txt": 6, "bot_access": 6, "html_structure": 4,
            "schema_ld": 5, "llms_txt": 0, "ssr": 3, "machine_surfaces": 2,
            "pagespeed": 3, "canonical": 5, "indexing": 5, "search_discovery": 3,
        },
        agent_passed=_BASE_CORE_LABELS + _PROTOCOL_LABELS,
    ),
]


def _sample_benchmark_profiles() -> list[dict]:
    """Return v2 profile dicts for the bundled sample seeds (validation happens
    at loader time so the runtime never sees malformed sample data).
    """
    return [dict(seed) for seed in _SAMPLE_BENCHMARK_SEEDS]


# --- Loader (parses v2 envelope) -------------------------------------------

def _configured_benchmark_profile_path() -> Path | None:
    configured_path = os.getenv("MACHINEREAD_BENCHMARK_PROFILE_PATH")
    if configured_path:
        return Path(configured_path).expanduser()
    return None


def _load_benchmark_profiles() -> tuple[BenchmarkProfile, ...]:
    """Parse v2 envelope. Eager validation. Fail loud on malformed input.

    Returns an immutable tuple of dicts so callers cannot mutate the
    snapshot at runtime.
    """
    configured_path = _configured_benchmark_profile_path()
    if configured_path:
        if not configured_path.exists():
            raise FileNotFoundError(f"Benchmark profile file not found: {configured_path}")
        payload = json.loads(configured_path.read_text(encoding="utf-8"))
    elif _DEFAULT_BENCHMARK_PROFILE_PATH.exists():
        payload = json.loads(_DEFAULT_BENCHMARK_PROFILE_PATH.read_text(encoding="utf-8"))
    else:
        return tuple(_sample_benchmark_profiles())

    # Detect v1 (bare list) shape and refuse to migrate
    if isinstance(payload, list):
        raise ValueError(
            "Benchmark profile file is in the deprecated v1 (bare list) format. "
            "Run `python scripts/refresh_benchmarks.py --peers "
            "scripts/benchmark_peers.sample.json --out <path>` to migrate."
        )
    if not isinstance(payload, dict):
        raise ValueError(
            f"Benchmark profile file: expected object, got {type(payload).__name__}"
        )
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"Benchmark profile file: schema_version {payload.get('schema_version')!r} "
            f"!= required {SCHEMA_VERSION}"
        )
    profiles = payload.get("profiles")
    if not isinstance(profiles, list):
        raise ValueError("Benchmark profile file: 'profiles' must be a list")

    valid_check_names = {g.check_name for g in ESSENTIALS_CHECK_GROUPS}
    rubric_max = {g.check_name: g.max_score for g in ESSENTIALS_CHECK_GROUPS}
    validated: list[BenchmarkProfile] = []
    for profile in profiles:
        _validate_profile(profile, valid_check_names, rubric_max)
        validated.append({
            "name": profile["name"],
            "category": profile["category"],
            "group": profile["group"],
            "size": profile["size"],
            "url": profile["url"],
            "essentials_checks": {
                k: _CheckSnapshot(
                    score=int(v["score"]),
                    max_score=int(v["max"]),
                    state=v["state"],
                    evidence_level=v["evidence_level"],
                )
                for k, v in profile["essentials_checks"].items()
            },
            "agent_passed": tuple(profile["agent_passed"]),
        })
    return tuple(validated)


_BENCHMARK_PROFILES: tuple[BenchmarkProfile, ...] = _load_benchmark_profiles()


def _loaded_snapshot_date() -> str:
    """Read the snapshot date from the loaded profile file, or fall back to
    the bundled constant. The bundled sample seeds have no file on disk,
    so they fall back.
    """
    configured_path = _configured_benchmark_profile_path()
    if configured_path and configured_path.exists():
        payload = json.loads(configured_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return str(payload.get("snapshot_date") or BENCHMARK_SNAPSHOT_DATE)
    if _DEFAULT_BENCHMARK_PROFILE_PATH.exists():
        payload = json.loads(_DEFAULT_BENCHMARK_PROFILE_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return str(payload.get("snapshot_date") or BENCHMARK_SNAPSHOT_DATE)
    return BENCHMARK_SNAPSHOT_DATE


# --- Entry construction at query time -------------------------------------

def _entries(
    include_protocols: bool,
    include_account_auth: bool,
    include_ecommerce: bool,
) -> list[BenchmarkEntry]:
    """Build BenchmarkEntry per profile for the current scope.

    Replaces the v1 ``_entries_for_scope(scope_key)`` which keyed off
    pre-baked per-scope variants. Scope is now derived from the three
    booleans at call time.
    """
    entries: list[BenchmarkEntry] = []
    for profile in _BENCHMARK_PROFILES:
        checked_score, checked_max, pillar_scores = peer_essentials(profile)
        agent_earned, agent_max = peer_agent(
            profile, include_protocols, include_account_auth, include_ecommerce
        )
        free_evidence_score = (
            round((checked_score / checked_max) * 100) if checked_max else 0
        )
        agent_readiness_score = (
            round((agent_earned / agent_max) * 100) if agent_max else 0
        )
        entries.append(
            BenchmarkEntry(
                name=profile["name"],
                category=profile["category"],
                group=profile["group"],
                size=profile["size"],
                url=profile["url"],
                overall_score=checked_score,
                free_evidence_score=free_evidence_score,
                checked_score=checked_score,
                checked_max=checked_max,
                agent_readiness_score=agent_readiness_score,
                agent_readiness_earned=agent_earned,
                agent_readiness_max=agent_max,
                pillar_scores=pillar_scores,
            )
        )
    return entries


# --- _checked_points (KEEP) and free_evidence_score (KEEP) -----------------

def _checked_points(checks: list[CheckResult]) -> tuple[int, int]:
    # Exclude paid-tier locked rows AND inconclusive (warn/unknown-evidence) rows.
    # Warn-state fallbacks (e.g. transient fetch failures from _fallback_check_result)
    # carry evidence_level='unknown' and score=0/max_score=group.max_score. Keeping
    # them in the denominator silently drags the Evidence score down even when the
    # real site satisfied every other check. Treat them as out-of-scope for the
    # benchmark the same way reserved/paid rows are excluded.
    checked = [
        check
        for check in checks
        if check.state != "locked" and check.evidence_level != "unknown"
    ]
    return (
        sum(check.score for check in checked),
        sum(check.max_score for check in checked),
    )


def free_evidence_score(checks: list[CheckResult]) -> int:
    checked_score, checked_max = _checked_points(checks)
    if not checked_max:
        return 0
    return round((checked_score / checked_max) * 100)


# --- Percentile / position helpers (KEEP unchanged) ------------------------

def _percentile(score: int, entries: list[BenchmarkEntry]) -> int:
    if not entries:
        return 0
    at_or_below = sum(entry.free_evidence_score <= score for entry in entries)
    return round((at_or_below / len(entries)) * 100)


def _agent_percentile(score: int, entries: list[BenchmarkEntry]) -> int:
    if not entries:
        return 0
    at_or_below = sum(entry.agent_readiness_score <= score for entry in entries)
    return round((at_or_below / len(entries)) * 100)


def _position_label(score: int, entries: list[BenchmarkEntry]) -> str:
    scores = sorted(entry.free_evidence_score for entry in entries)
    lower_quartile = scores[len(scores) // 4]
    upper_quartile = scores[(len(scores) * 3) // 4]
    median_score = round(median(scores))

    if score >= upper_quartile:
        return "Near the top of this Essentials benchmark snapshot"
    if score >= median_score:
        return "Above the middle of this Essentials benchmark snapshot"
    if score >= lower_quartile:
        return "Within the middle of this Essentials benchmark snapshot"
    return "Below most sites in this Essentials benchmark snapshot"


def _agent_position_label(score: int, entries: list[BenchmarkEntry]) -> str:
    scores = sorted(entry.agent_readiness_score for entry in entries)
    lower_quartile = scores[len(scores) // 4]
    upper_quartile = scores[(len(scores) * 3) // 4]
    median_score = round(median(scores))

    if score >= upper_quartile:
        return "Near the top of this strict agent-readiness snapshot"
    if score >= median_score:
        return "Above the middle of this strict agent-readiness snapshot"
    if score >= lower_quartile:
        return "Within the middle of this strict agent-readiness snapshot"
    return "Below most sites in this strict agent-readiness snapshot"


# --- Public API: build_benchmark_comparison --------------------------------

def build_benchmark_comparison(
    checks: list[CheckResult],
    include_protocols: bool = False,
    include_account_auth: bool = False,
    include_ecommerce: bool = False,
) -> BenchmarkComparison:
    snapshot_date = _loaded_snapshot_date()
    entries = sorted(
        _entries(include_protocols, include_account_auth, include_ecommerce),
        key=lambda entry: (-entry.free_evidence_score, entry.name),
    )
    if not entries:
        return BenchmarkComparison(
            score=0,
            checked_score=0,
            checked_max=0,
            benchmark_count=0,
            median_score=0,
            percentile=0,
            position_label="No peers available",
            nearest=[],
            entries=[],
            basis=BENCHMARK_BASIS,
            snapshot_date=snapshot_date,
            caveat=BENCHMARK_CAVEAT,
        )

    checked_score, checked_max = _checked_points(checks)
    score = free_evidence_score(checks)
    nearest = sorted(
        entries,
        key=lambda entry: (abs(entry.free_evidence_score - score), -entry.free_evidence_score, entry.name),
    )[:3]

    return BenchmarkComparison(
        score=score,
        checked_score=checked_score,
        checked_max=checked_max,
        benchmark_count=len(entries),
        median_score=round(median(entry.free_evidence_score for entry in entries)),
        percentile=_percentile(score, entries),
        position_label=_position_label(score, entries),
        nearest=nearest,
        entries=entries,
        basis=BENCHMARK_BASIS,
        snapshot_date=snapshot_date,
        caveat=BENCHMARK_CAVEAT,
    )


def build_agent_benchmark_comparison(
    score: int,
    earned: int,
    maximum: int,
    include_protocols: bool = False,
    include_account_auth: bool = False,
    include_ecommerce: bool = False,
) -> AgentBenchmarkComparison:
    snapshot_date = _loaded_snapshot_date()
    entries = sorted(
        _entries(include_protocols, include_account_auth, include_ecommerce),
        key=lambda entry: (-entry.agent_readiness_score, entry.name),
    )
    if not entries:
        return AgentBenchmarkComparison(
            score=0,
            earned=0,
            max=0,
            benchmark_count=0,
            median_score=0,
            percentile=0,
            position_label="No peers available",
            nearest=[],
            entries=[],
            basis=AGENT_BENCHMARK_BASIS,
            snapshot_date=snapshot_date,
            caveat=AGENT_BENCHMARK_CAVEAT,
        )

    nearest = sorted(
        entries,
        key=lambda entry: (abs(entry.agent_readiness_score - score), -entry.agent_readiness_score, entry.name),
    )[:3]

    return AgentBenchmarkComparison(
        score=score,
        earned=earned,
        max=maximum,
        benchmark_count=len(entries),
        median_score=round(median(entry.agent_readiness_score for entry in entries)),
        percentile=_agent_percentile(score, entries),
        position_label=_agent_position_label(score, entries),
        nearest=nearest,
        entries=entries,
        basis=AGENT_BENCHMARK_BASIS,
        snapshot_date=snapshot_date,
        caveat=AGENT_BENCHMARK_CAVEAT,
    )
