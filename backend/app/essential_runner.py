"""Shared helpers for the Essentials audit pipeline.

This module hosts the helper functions used by ``app.main`` to build
the audit response. Keeping the functions here means a single fix
lands in the audit pipeline without code duplication.
"""

from collections.abc import Awaitable

from app.agent_readiness import _scope_probe_labels, build_agent_readiness_summary
from app.benchmarks import build_agent_benchmark_comparison
from app.models import AgentReadinessSummary, CheckResult
from app.rubric import EssentialsCheckGroup


def _agent_readiness_max(
    include_protocols: bool,
    include_account_auth: bool,
    include_ecommerce: bool,
) -> int:
    # Strict-probe fallback max.
    # Core=8, protocols=6 (was 5; ARD added as 6th protocol probe), account_auth=3, commerce=4.
    # Full-scope total is 8+6+3+4=21; default-scope is 8.
    return 8 + (6 if include_protocols else 0) + (3 if include_account_auth else 0) + (
        4 if include_ecommerce else 0
    )


def _fallback_check_result(group: EssentialsCheckGroup) -> CheckResult:
    return CheckResult(
        pillar=group.pillar,
        check_name=group.check_name,
        label="Check unavailable",
        state="warn",
        evidence_level="unknown",
        score=0,
        max_score=group.max_score,
        finding=(
            "MachineRead could not complete this check during the audit, so this row is "
            "inconclusive rather than verified site evidence."
        ),
        fix=(
            "Retry the audit. If this warning repeats, review server logs for this check "
            "before relying on the score."
        ),
        effort="medium",
    )


async def _safe_check(
    group: EssentialsCheckGroup,
    check: Awaitable[CheckResult],
    *,
    logger=None,
) -> CheckResult:
    try:
        result = await check
        if not isinstance(result, CheckResult):
            raise TypeError(f"{group.check_name} returned {type(result).__name__}")
        return result
    except Exception:
        if logger is not None:
            logger.exception("audit check failed: %s", group.check_name)
        return _fallback_check_result(group)


def _fallback_agent_readiness_summary(
    include_protocols: bool,
    include_account_auth: bool,
    include_ecommerce: bool,
) -> AgentReadinessSummary:
    maximum = _agent_readiness_max(include_protocols, include_account_auth, include_ecommerce)
    probe_labels = _scope_probe_labels(include_protocols, include_account_auth, include_ecommerce)
    return AgentReadinessSummary(
        score=0,
        earned=0,
        max=maximum,
        label="Agent-native unavailable",
        categories=[],
        passed=[],
        missing=probe_labels,
        not_checked=[
            "Retry the audit. If this repeats, review server logs before treating agent-native readiness as measured.",
        ],
        benchmark=build_agent_benchmark_comparison(
            0,
            0,
            maximum,
            include_protocols,
            include_account_auth,
            include_ecommerce,
        ),
        caveat=(
            "MachineRead could not complete the strict agent-native lens for this run, so "
            "this section is an actionable audit warning rather than verified evidence "
            "about the site."
        ),
    )


async def _safe_agent_readiness_summary(
    context,
    include_protocols: bool,
    include_account_auth: bool,
    include_ecommerce: bool,
    *,
    logger=None,
) -> AgentReadinessSummary:
    try:
        return await build_agent_readiness_summary(
            context,
            include_protocols,
            include_account_auth,
            include_ecommerce,
        )
    except Exception:
        if logger is not None:
            logger.exception("agent-readiness summary failed: %s", context.url)
        return _fallback_agent_readiness_summary(
            include_protocols,
            include_account_auth,
            include_ecommerce,
        )