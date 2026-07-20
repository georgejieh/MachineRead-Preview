import json
import os
from itertools import product
from pathlib import Path
from statistics import median
from typing import Any

from app.models import AgentBenchmarkComparison, BenchmarkComparison, BenchmarkEntry, CheckResult, PillarScores
from app.rubric import ESSENTIALS_CHECKED_MAX, ESSENTIALS_CHECK_GROUP_COUNT

BENCHMARK_SNAPSHOT_DATE = "2026-07-17"
BENCHMARK_BASIS = "Essentials evidence score from the MachineRead audit"
AGENT_BENCHMARK_BASIS = "Agent-native readiness score from the MachineRead audit"
BENCHMARK_CAVEAT = (
    "Benchmark peers are scored under the same selected Essentials scope using bounded "
    "public HTTP, DNS, and page evidence. Comparable scores do not imply comparable AI "
    "exposure, search traffic, ecommerce conversion, ranking strength, backlinks, social "
    "traction, real Google/Bing/Brave index coverage, Core Web Vitals field data, or live "
    "model citation share. "
    f"The current Essentials denominator is {ESSENTIALS_CHECKED_MAX} checked points across "
    f"{ESSENTIALS_CHECK_GROUP_COUNT} included check groups; each group may aggregate "
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

_DEFAULT_BENCHMARK_PROFILE_PATH = (
    Path(__file__).resolve().parents[1] / "private_data" / "benchmark_profiles.json"
)
_SAMPLE_BENCHMARK_SEEDS: tuple[
    tuple[str, str, str, str, str, int, int, int, int],
    ...,
] = (
    (
        "Example Retail Enterprise",
        "Major retailer",
        "commerce",
        "enterprise",
        "https://example.com/retail",
        5,
        25,
        14,
        3,
    ),
    (
        "Example Consumer Brand",
        "Major consumer brand",
        "commerce",
        "enterprise",
        "https://example.com/consumer",
        5,
        22,
        14,
        2,
    ),
    (
        "Example Commerce Platform",
        "Commerce platform",
        "commerce",
        "enterprise",
        "https://example.com/platform",
        6,
        26,
        16,
        5,
    ),
    (
        "Example Product Studio",
        "Niche DTC product",
        "commerce",
        "specialty",
        "https://example.com/product",
        4,
        24,
        15,
        3,
    ),
    (
        "Example Beauty Brand",
        "Niche beauty brand",
        "commerce",
        "specialty",
        "https://example.com/beauty",
        4,
        24,
        14,
        3,
    ),
    (
        "Example Outdoor Shop",
        "Specialty commerce",
        "commerce",
        "specialty",
        "https://example.com/outdoor",
        2,
        14,
        8,
        1,
    ),
    (
        "Example Paper Goods",
        "Boutique product",
        "commerce",
        "boutique",
        "https://example.com/paper",
        4,
        18,
        16,
        2,
    ),
    (
        "Example Desk Goods",
        "Boutique product",
        "commerce",
        "boutique",
        "https://example.com/desk",
        2,
        8,
        4,
        1,
    ),
    (
        "Example Technology Company",
        "Major technology brand",
        "corporate",
        "enterprise",
        "https://example.com/technology",
        4,
        25,
        14,
        3,
    ),
    (
        "Example Developer Platform",
        "Developer platform",
        "corporate",
        "specialty",
        "https://example.com/developer",
        6,
        26,
        16,
        5,
    ),
    (
        "Example Software Studio",
        "Software company",
        "corporate",
        "boutique",
        "https://example.com/software",
        4,
        21,
        11,
        2,
    ),
    (
        "Example Advisory Firm",
        "Professional services",
        "service",
        "enterprise",
        "https://example.com/advisory",
        4,
        24,
        14,
        3,
    ),
    (
        "Example Scheduling Service",
        "SaaS service",
        "service",
        "specialty",
        "https://example.com/scheduling",
        6,
        26,
        16,
        5,
    ),
    (
        "Example Analytics Service",
        "Analytics service",
        "service",
        "boutique",
        "https://example.com/analytics",
        6,
        26,
        16,
        5,
    ),
)


def benchmark_scope_key(
    include_protocols: bool = False,
    include_account_auth: bool = False,
    include_ecommerce: bool = False,
) -> str:
    return f"p{int(include_protocols)}_a{int(include_account_auth)}_c{int(include_ecommerce)}"


def _agent_max_for_scope(
    include_protocols: bool = False,
    include_account_auth: bool = False,
    include_ecommerce: bool = False,
) -> int:
    return 8 + (6 if include_protocols else 0) + (3 if include_account_auth else 0) + (
        4 if include_ecommerce else 0
    )


def _configured_benchmark_profile_path() -> Path | None:
    configured_path = os.getenv("MACHINEREAD_BENCHMARK_PROFILE_PATH")
    if configured_path:
        return Path(configured_path).expanduser()
    return None


def _sample_variant(
    off_site: int,
    scrapability: int,
    seo: int,
    agent_earned: int,
    include_protocols: bool,
    include_account_auth: bool,
    include_ecommerce: bool,
) -> dict[str, Any]:
    checked_score = off_site + scrapability + seo
    agent_max = _agent_max_for_scope(include_protocols, include_account_auth, include_ecommerce)
    return {
        "overall_score": checked_score,
        "free_evidence_score": round((checked_score / ESSENTIALS_CHECKED_MAX) * 100),
        "checked_score": checked_score,
        "checked_max": ESSENTIALS_CHECKED_MAX,
        "agent_readiness_score": round((agent_earned / agent_max) * 100),
        "agent_readiness_earned": agent_earned,
        "agent_readiness_max": agent_max,
        "pillar_scores": {
            "off_site": off_site,
            "scrapability": scrapability,
            "seo": seo,
        },
    }


def _sample_benchmark_profiles() -> list[BenchmarkProfile]:
    profiles = []
    for name, category, group, size, url, off_site, scrapability, seo, agent_earned in _SAMPLE_BENCHMARK_SEEDS:
        variants = {}
        for include_protocols, include_account_auth, include_ecommerce in product((False, True), repeat=3):
            key = benchmark_scope_key(include_protocols, include_account_auth, include_ecommerce)
            variants[key] = _sample_variant(
                off_site,
                scrapability,
                seo,
                agent_earned,
                include_protocols,
                include_account_auth,
                include_ecommerce,
            )
        profiles.append(
            {
                "name": name,
                "category": category,
                "group": group,
                "size": size,
                "url": url,
                "variants": variants,
            }
        )
    return profiles


def _load_benchmark_profiles() -> list[BenchmarkProfile]:
    configured_path = _configured_benchmark_profile_path()
    if configured_path:
        if not configured_path.exists():
            raise FileNotFoundError(f"Benchmark profile file not found: {configured_path}")
        return json.loads(configured_path.read_text(encoding="utf-8"))

    if _DEFAULT_BENCHMARK_PROFILE_PATH.exists():
        return json.loads(_DEFAULT_BENCHMARK_PROFILE_PATH.read_text(encoding="utf-8"))
    return _sample_benchmark_profiles()


_BENCHMARK_PROFILES: list[BenchmarkProfile] = _load_benchmark_profiles()


def _scope_flags_from_key(scope_key: str) -> tuple[bool, bool, bool]:
    parts = set(scope_key.split("_"))
    return (
        "p1" in parts,
        "a1" in parts,
        "c1" in parts,
    )


def _entry_from_profile(profile: BenchmarkProfile, scope_key: str) -> BenchmarkEntry:
    variant = profile["variants"][scope_key]
    include_protocols, include_account_auth, include_ecommerce = _scope_flags_from_key(scope_key)
    agent_max = _agent_max_for_scope(include_protocols, include_account_auth, include_ecommerce)
    agent_earned = min(int(variant["agent_readiness_earned"]), agent_max)
    return BenchmarkEntry(
        name=profile["name"],
        category=profile["category"],
        group=profile["group"],
        size=profile["size"],
        url=profile["url"],
        overall_score=variant["overall_score"],
        free_evidence_score=variant["free_evidence_score"],
        checked_score=variant["checked_score"],
        checked_max=variant["checked_max"],
        agent_readiness_score=round((agent_earned / agent_max) * 100) if agent_max else 0,
        agent_readiness_earned=agent_earned,
        agent_readiness_max=agent_max,
        pillar_scores=PillarScores(**variant["pillar_scores"]),
    )


def _entries_for_scope(scope_key: str) -> list[BenchmarkEntry]:
    return [
        _entry_from_profile(profile, scope_key)
        for profile in _BENCHMARK_PROFILES
        if scope_key in profile["variants"]
    ]


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


def build_benchmark_comparison(
    checks: list[CheckResult],
    include_protocols: bool = False,
    include_account_auth: bool = False,
    include_ecommerce: bool = False,
) -> BenchmarkComparison:
    scope_key = benchmark_scope_key(include_protocols, include_account_auth, include_ecommerce)
    entries = sorted(
        _entries_for_scope(scope_key),
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
            snapshot_date=BENCHMARK_SNAPSHOT_DATE,
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
        snapshot_date=BENCHMARK_SNAPSHOT_DATE,
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
    scope_key = benchmark_scope_key(include_protocols, include_account_auth, include_ecommerce)
    entries = sorted(
        _entries_for_scope(scope_key),
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
            snapshot_date=BENCHMARK_SNAPSHOT_DATE,
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
        snapshot_date=BENCHMARK_SNAPSHOT_DATE,
        caveat=AGENT_BENCHMARK_CAVEAT,
    )
