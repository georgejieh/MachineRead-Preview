"""F3-12: agent-oriented compact ``AuditSummary`` projection.

The projection is a pure, deterministic function from
:class:`app.models.AuditResult` to :class:`app.models.AuditSummary`. It does
not perform any network, file I/O, LLM call, or prose generation.

The compact surface is intentionally smaller than the full report so an
agent can decide whether a full ``/v1/audit`` call is worth its context
budget without first paying for one. The following source fields are
intentionally omitted from the projection and must not leak into the agent
surface:

- Per-check ``finding`` and ``fix`` prose.
- Display ``label`` strings for check rows (the agent uses ``check_name``).
- ``BenchmarkComparison.entries`` and ``BenchmarkComparison.nearest``
  (peer profile data is private/public-safe at the broader surface, but
  the summary endpoint advertises only percentile, median, peer count,
  and snapshot).
- ``BenchmarkComparison.basis`` and ``BenchmarkComparison.caveat`` prose.
- ``AgentReadinessSummary.passed`` / ``missing`` / ``not_checked`` lists.
- ``AuditResult.checks[*].finding`` and ``AuditResult.checks[*].fix``.
- Rate-limit body metadata and any raw fetch context.
"""

from __future__ import annotations

from app.models import (
    AuditResult,
    AuditSummary,
    SUMMARY_LIMITATION_CODES,
    SummaryAttentionItem,
    SummaryBenchmark,
    SummaryCheckCounts,
    SummaryScorePair,
    SummaryScores,
    SummaryScope,
)

# Cap matches the brief: at most 5 attention rows in the 200 payload.
_ATTENTION_CAP = 5

# Severity rank: lower index = more urgent. ``fail`` first, then ``partial``,
# then ``warn``. ``pass`` and ``locked`` are excluded from attention.
_SEVERITY_RANK: dict[str, int] = {
    "fail": 0,
    "partial": 1,
    "warn": 2,
}


def _ratio(row_earned: int, row_max: int) -> float:
    """Return the attention ratio for a row.

    Rows with ``max == 0`` are reported first in their severity tier
    because the agent cannot recover any points from them — their ratio
    is effectively ``0``.
    """

    if row_max <= 0:
        return 0.0
    return row_earned / row_max


def _select_attention(result: AuditResult) -> list[SummaryAttentionItem]:
    """Pick up to :data:`_ATTENTION_CAP` rows for the ``attention`` array.

    Selection rules from the brief:

    - Exclude rows with ``state == "locked"`` (advanced coverage rows).
    - Exclude rows with ``state == "pass"`` (no remediation needed).
    - Sort the remaining rows by:

        1. severity rank (``fail`` < ``partial`` < ``warn``),
        2. attention ratio ascending (lower ratio = more urgent),
        3. ``check_name`` ascending (stable alphabetical tie-break).

    - Cap the result at :data:`_ATTENTION_CAP` rows.
    """

    candidates: list[SummaryAttentionItem] = []
    for row in result.checks:
        if row.state == "locked" or row.state == "pass":
            continue
        if row.state not in _SEVERITY_RANK:
            continue
        # ``evidence_level`` from the source is one of
        # ``"verified" | "inferred" | "unknown" | "not_applicable"``;
        # map ``unknown`` to ``"unavailable"`` so the strict Literal
        # type is satisfied. ``not_applicable`` is the contract value
        # for locked rows, so it should not appear here in practice.
        evidence_level = row.evidence_level
        if evidence_level == "unknown":
            evidence_level = "unavailable"
        elif evidence_level == "not_applicable":
            evidence_level = "unavailable"
        candidates.append(
            SummaryAttentionItem(
                check_name=row.check_name,
                pillar=row.pillar,
                state=row.state,
                evidence_level=evidence_level,
                earned=row.score,
                max=row.max_score,
                effort=row.effort,
            )
        )

    candidates.sort(
        key=lambda item: (
            _SEVERITY_RANK[item.state],
            _ratio(item.earned, item.max),
            item.check_name,
        )
    )
    return candidates[:_ATTENTION_CAP]


def _check_counts(
    result: AuditResult,
    *,
    attention_total: int,
) -> SummaryCheckCounts:
    included = [row for row in result.checks if row.state != "locked"]
    locked = [row for row in result.checks if row.state == "locked"]
    pass_count = sum(1 for row in included if row.state == "pass")
    partial = sum(1 for row in included if row.state == "partial")
    fail = sum(1 for row in included if row.state == "fail")
    warn = sum(1 for row in included if row.state == "warn")
    return SummaryCheckCounts(
        included=len(included),
        locked=len(locked),
        pass_count=pass_count,
        partial=partial,
        fail=fail,
        warn=warn,
        attention_total=attention_total,
    )


def build_report_summary(result: AuditResult) -> AuditSummary:
    """Project a full :class:`AuditResult` into a compact
    :class:`AuditSummary`.

    The function is pure: no network, no file I/O, no logging, no
    prose. It is safe to call from a unit test or a cache layer.
    """

    attention = _select_attention(result)
    counts = _check_counts(result, attention_total=len(attention))

    overall = SummaryScorePair(
        earned=result.overall_score,
        max=result.pillar_max.off_site
        + result.pillar_max.scrapability
        + result.pillar_max.seo,
    )
    pillars = {
        "off_site": SummaryScorePair(
            earned=result.pillar_scores.off_site,
            max=result.pillar_max.off_site,
        ),
        "scrapability": SummaryScorePair(
            earned=result.pillar_scores.scrapability,
            max=result.pillar_max.scrapability,
        ),
        "seo": SummaryScorePair(
            earned=result.pillar_scores.seo,
            max=result.pillar_max.seo,
        ),
    }
    scores = SummaryScores(
        overall=overall,
        pillars=pillars,
        essentials={
            "percent": result.benchmark.score,
            "earned": result.benchmark.checked_score,
            "max": result.benchmark.checked_max,
        },
        agent_readiness={
            # Use the source ``agent_readiness.score`` verbatim. The
            # summary endpoint must never recompute this from earned/max
            # because the runtime and the public benchmark entries round
            # the score with the same rule (round-half-to-even via
            # ``round(earned / max * 100)``), but the project should
            # still copy the value rather than re-derive it.
            "percent": result.agent_readiness.score,
            "earned": result.agent_readiness.earned,
            "max": result.agent_readiness.max,
        },
    )

    benchmarks = {
        "essentials": SummaryBenchmark(
            percentile=result.benchmark.percentile,
            median_percent=result.benchmark.median_score,
            peer_count=result.benchmark.benchmark_count,
            snapshot=result.benchmark.snapshot_date,
        ),
        "agent_readiness": SummaryBenchmark(
            percentile=result.agent_readiness.benchmark.percentile,
            median_percent=result.agent_readiness.benchmark.median_score,
            peer_count=result.agent_readiness.benchmark.benchmark_count,
            snapshot=result.agent_readiness.benchmark.snapshot_date,
        ),
    }

    scope = SummaryScope(
        preset=result.scope.preset_applied,
        protocols=result.scope.include_protocols,
        account_auth=result.scope.include_account_auth,
        ecommerce=result.scope.include_ecommerce,
        overrides=dict(result.scope.overrides_applied),
    )

    return AuditSummary(
        api_version="1.0",
        summary_version="1.0",
        url=result.url,
        scope=scope,
        scores=scores,
        benchmarks=benchmarks,
        checks=counts,
        attention=attention,
        limitations=SUMMARY_LIMITATION_CODES,
    )
