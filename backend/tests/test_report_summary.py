"""Projection tests for the agent-oriented report summary.

These tests build synthetic ``AuditResult`` fixtures and call
``build_report_summary`` directly. The expected contract is captured in the
brief: 13 check groups, 56 checked points, 30/40/30 pillars, 8 scope variants,
max 5 attention items, and a fixed ordered tuple of limitation codes.
"""

from __future__ import annotations

import sys
import unittest
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import (
    AgentBenchmarkComparison,
    AgentReadinessCategory,
    AgentReadinessSummary,
    AuditResult,
    AuditScope,
    BenchmarkComparison,
    BenchmarkEntry,
    CheckResult,
    PillarMax,
    PillarScores,
)
from app.report_summary import (
    SUMMARY_LIMITATION_CODES,
    build_report_summary,
)
from app.checks.locked import locked_checks


def _benchmark(*, score: int = 50, snapshot: str = "2026-07-18") -> BenchmarkComparison:
    return BenchmarkComparison(
        score=score,
        checked_score=score,
        checked_max=56,
        benchmark_count=3,
        median_score=score,
        percentile=50,
        position_label="At median",
        nearest=[],
        entries=[],
        basis="Public fallback benchmark basis.",
        snapshot_date=snapshot,
        caveat="Benchmark positions are relative context among public peers.",
    )


def _agent_readiness(
    *,
    include_protocols: bool,
    include_account_auth: bool,
    include_ecommerce: bool,
    score: int = 50,
    earned: int = 4,
) -> AgentReadinessSummary:
    max_value = (
        8
        + (6 if include_protocols else 0)
        + (3 if include_account_auth else 0)
        + (4 if include_ecommerce else 0)
    )
    return AgentReadinessSummary(
        score=score,
        earned=earned,
        max=max_value,
        label="Developing agent-native readiness",
        categories=[
            AgentReadinessCategory(
                name="Discovery",
                earned=2,
                max=3,
                score=round((2 / 3) * 100),
                passed=["robots.txt published"],
                missing=["DNS-AID records"],
                excluded=[],
            )
        ],
        passed=["robots.txt published"],
        missing=["DNS-AID records"],
        not_checked=["Verified crawler IP treatment"],
        benchmark=AgentBenchmarkComparison(
            score=score,
            earned=earned,
            max=max_value,
            benchmark_count=3,
            median_score=score,
            percentile=50,
            position_label="At median",
            nearest=[],
            entries=[],
            basis="Public fallback agent benchmark basis.",
            snapshot_date="2026-07-18",
            caveat="Strict agent-readiness positions are relative context.",
        ),
        caveat="Strict agent-readiness does not imply agent routing or citation share.",
    )


def _check(
    check_name: str,
    pillar: str,
    *,
    state: str,
    earned: int = 0,
    max_score: int = 4,
    effort: str = "medium",
    evidence_level: str = "verified",
) -> CheckResult:
    return CheckResult(
        pillar=pillar,
        check_name=check_name,
        label=check_name.replace("_", " ").title(),
        state=state,
        evidence_level=evidence_level,
        available_in="Essentials",
        score=earned,
        max_score=max_score,
        finding=f"{check_name} finding",
        fix=f"{check_name} fix",
        effort=effort,
    )


def _build_result(
    *,
    include_protocols: bool,
    include_account_auth: bool,
    include_ecommerce: bool,
    preset_applied: str | None = "blog",
    checks: list[CheckResult] | None = None,
) -> AuditResult:
    if checks is None:
        checks = [
            _check("robots_txt", "scrapability", state="partial", earned=4, max_score=6),
            _check("bot_access", "scrapability", state="partial", earned=4, max_score=6),
            _check("html_structure", "scrapability", state="pass", earned=4, max_score=4),
            _check("schema_ld", "scrapability", state="partial", earned=3, max_score=5),
            _check("llms_txt", "scrapability", state="partial", earned=3, max_score=5),
            _check("ssr", "scrapability", state="pass", earned=4, max_score=4),
            _check("machine_surfaces", "scrapability", state="partial", earned=2, max_score=3),
            _check("pagespeed", "seo", state="partial", earned=2, max_score=3),
            _check("canonical", "seo", state="partial", earned=4, max_score=5),
            _check("indexing", "seo", state="partial", earned=4, max_score=5),
            _check("search_discovery", "seo", state="partial", earned=3, max_score=4),
            _check("social", "off_site", state="partial", earned=1, max_score=2),
            _check("wikipedia", "off_site", state="partial", earned=2, max_score=4),
        ]
    # Always include the 9 locked rows so check counts and locked
    # counts match the runtime contract.
    checks = list(checks) + list(locked_checks(include_ecommerce))
    scope = AuditScope(
        include_protocols=include_protocols,
        include_account_auth=include_account_auth,
        include_ecommerce=include_ecommerce,
        label="Blog/Content audit",
        included_optional_surfaces=[],
        excluded_optional_surfaces=[],
        preset_applied=preset_applied,
        overrides_applied={},
        included_families=[],
        excluded_families=[],
        machine_surfaces_scope="common-contextual",
    )
    pillar_scores = PillarScores(off_site=3, scrapability=24, seo=13)
    return AuditResult(
        api_version="1.0",
        url="https://example.com/",
        scope=scope,
        overall_score=pillar_scores.off_site + pillar_scores.scrapability + pillar_scores.seo,
        pillar_scores=pillar_scores,
        pillar_max=PillarMax(off_site=30, scrapability=40, seo=30),
        agent_readiness=_agent_readiness(
            include_protocols=include_protocols,
            include_account_auth=include_account_auth,
            include_ecommerce=include_ecommerce,
        ),
        benchmark=_benchmark(),
        checks=checks,
    )


class ProjectionSmokeTests(unittest.TestCase):
    def test_full_projection_smoke(self) -> None:
        result = _build_result(
            include_protocols=False,
            include_account_auth=False,
            include_ecommerce=False,
        )
        summary = build_report_summary(result)

        self.assertEqual(summary.api_version, "1.0")
        self.assertEqual(summary.summary_version, "1.0")
        self.assertEqual(summary.url, "https://example.com/")
        self.assertEqual(summary.scores.overall.earned, result.overall_score)
        self.assertEqual(summary.scores.overall.max, 100)
        self.assertEqual(summary.scores.essentials.percent, result.benchmark.score)
        self.assertEqual(summary.scores.essentials.earned, result.benchmark.checked_score)
        self.assertEqual(summary.scores.essentials.max, result.benchmark.checked_max)
        self.assertEqual(
            summary.scores.agent_readiness.percent,
            result.agent_readiness.score,
        )
        self.assertEqual(summary.scores.agent_readiness.earned, result.agent_readiness.earned)
        self.assertEqual(summary.scores.agent_readiness.max, result.agent_readiness.max)
        self.assertEqual(
            set(summary.benchmarks),
            {"essentials", "agent_readiness"},
        )
        self.assertEqual(
            summary.benchmarks["essentials"].snapshot,
            result.benchmark.snapshot_date,
        )
        self.assertEqual(
            summary.benchmarks["essentials"].median_percent,
            result.benchmark.median_score,
        )
        self.assertEqual(
            summary.benchmarks["essentials"].peer_count,
            result.benchmark.benchmark_count,
        )
        self.assertEqual(
            summary.benchmarks["essentials"].percentile,
            result.benchmark.percentile,
        )
        self.assertEqual(
            summary.benchmarks["agent_readiness"].snapshot,
            result.agent_readiness.benchmark.snapshot_date,
        )
        self.assertEqual(summary.limitations, SUMMARY_LIMITATION_CODES)
        self.assertEqual(
            summary.limitations,
            (
                "relative_scores",
                "no_live_ranking",
                "no_provider_ip_auth",
                "no_paid_crawlers",
            ),
        )


class ScorePreservationTests(unittest.TestCase):
    def test_score_pairs_preserve_source_numerics(self) -> None:
        result = _build_result(
            include_protocols=True,
            include_account_auth=True,
            include_ecommerce=True,
        )
        summary = build_report_summary(result)
        self.assertEqual(
            summary.scores.overall.earned,
            result.overall_score,
        )
        self.assertEqual(summary.scores.overall.max, 100)
        self.assertEqual(
            summary.scores.pillars["off_site"].earned,
            result.pillar_scores.off_site,
        )
        self.assertEqual(
            summary.scores.pillars["off_site"].max,
            result.pillar_max.off_site,
        )
        self.assertEqual(
            summary.scores.pillars["scrapability"].earned,
            result.pillar_scores.scrapability,
        )
        self.assertEqual(
            summary.scores.pillars["scrapability"].max,
            result.pillar_max.scrapability,
        )
        self.assertEqual(
            summary.scores.pillars["seo"].earned,
            result.pillar_scores.seo,
        )
        self.assertEqual(
            summary.scores.pillars["seo"].max,
            result.pillar_max.seo,
        )
        self.assertEqual(
            summary.scores.essentials.percent,
            result.benchmark.score,
        )
        self.assertEqual(
            summary.scores.agent_readiness.percent,
            result.agent_readiness.score,
        )
        # Full-scope strict-agent max is 21 (8+6+3+4).
        self.assertEqual(summary.scores.agent_readiness.max, 21)


class AttentionSelectionTests(unittest.TestCase):
    def _fail_check(
        self,
        check_name: str,
        pillar: str,
        *,
        earned: int = 0,
        max_score: int = 4,
    ) -> CheckResult:
        return _check(
            check_name,
            pillar,
            state="fail",
            earned=earned,
            max_score=max_score,
        )

    def test_attention_capped_at_five_items(self) -> None:
        checks = [
            self._fail_check("robots_txt", "scrapability", earned=0, max_score=6),
            self._fail_check("bot_access", "scrapability", earned=0, max_score=6),
            self._fail_check("html_structure", "scrapability", earned=0, max_score=4),
            self._fail_check("schema_ld", "scrapability", earned=0, max_score=5),
            self._fail_check("llms_txt", "scrapability", earned=0, max_score=5),
            self._fail_check("ssr", "scrapability", earned=0, max_score=4),
            self._fail_check("machine_surfaces", "scrapability", earned=0, max_score=3),
            self._fail_check("pagespeed", "seo", earned=0, max_score=3),
            self._fail_check("canonical", "seo", earned=0, max_score=5),
            self._fail_check("indexing", "seo", earned=0, max_score=5),
            self._fail_check("search_discovery", "seo", earned=0, max_score=4),
            self._fail_check("social", "off_site", earned=0, max_score=2),
            self._fail_check("wikipedia", "off_site", earned=0, max_score=4),
        ]
        result = _build_result(
            include_protocols=False,
            include_account_auth=False,
            include_ecommerce=False,
            checks=checks,
        )
        summary = build_report_summary(result)
        self.assertEqual(len(summary.attention), 5)
        self.assertEqual(summary.checks.attention_total, 5)

    def test_severity_order_fail_before_partial_before_warn(self) -> None:
        checks = [
            _check("robots_txt", "scrapability", state="partial", earned=2, max_score=6),
            _check("bot_access", "scrapability", state="fail", earned=0, max_score=6),
            _check("html_structure", "scrapability", state="warn", earned=0, max_score=4),
            _check("schema_ld", "scrapability", state="partial", earned=2, max_score=5),
            _check("llms_txt", "scrapability", state="fail", earned=0, max_score=5),
            _check("ssr", "scrapability", state="partial", earned=1, max_score=4),
            _check("machine_surfaces", "scrapability", state="warn", earned=0, max_score=3),
            _check("pagespeed", "seo", state="fail", earned=0, max_score=3),
            _check("canonical", "seo", state="partial", earned=2, max_score=5),
            _check("indexing", "seo", state="warn", earned=0, max_score=5),
            _check("search_discovery", "seo", state="partial", earned=1, max_score=4),
            _check("social", "off_site", state="warn", earned=0, max_score=2),
            _check("wikipedia", "off_site", state="pass", earned=4, max_score=4),
        ]
        result = _build_result(
            include_protocols=False,
            include_account_auth=False,
            include_ecommerce=False,
            checks=checks,
        )
        summary = build_report_summary(result)
        states = [item.state for item in summary.attention]
        # The five-row cap is filled by the higher-severity fail and partial
        # clusters before any warn row can enter the projection.
        self.assertEqual(states, ["fail", "fail", "fail", "partial", "partial"])

    def test_ratio_ordering_within_severity(self) -> None:
        checks = [
            _check("robots_txt", "scrapability", state="fail", earned=5, max_score=6),
            _check("bot_access", "scrapability", state="fail", earned=1, max_score=6),
            _check("html_structure", "scrapability", state="fail", earned=3, max_score=4),
            _check("schema_ld", "scrapability", state="pass", earned=5, max_score=5),
            _check("llms_txt", "scrapability", state="pass", earned=5, max_score=5),
            _check("ssr", "scrapability", state="pass", earned=4, max_score=4),
            _check("machine_surfaces", "scrapability", state="pass", earned=3, max_score=3),
            _check("pagespeed", "seo", state="pass", earned=3, max_score=3),
            _check("canonical", "seo", state="pass", earned=5, max_score=5),
            _check("indexing", "seo", state="pass", earned=5, max_score=5),
            _check("search_discovery", "seo", state="pass", earned=4, max_score=4),
            _check("social", "off_site", state="pass", earned=2, max_score=2),
            _check("wikipedia", "off_site", state="pass", earned=4, max_score=4),
        ]
        result = _build_result(
            include_protocols=False,
            include_account_auth=False,
            include_ecommerce=False,
            checks=checks,
        )
        summary = build_report_summary(result)
        names = [item.check_name for item in summary.attention]
        self.assertEqual(
            names,
            ["bot_access", "html_structure", "robots_txt"],
        )
        # Lower earned/max ratio must surface first.
        self.assertEqual(summary.attention[0].earned, 1)
        self.assertEqual(summary.attention[1].earned, 3)
        self.assertEqual(summary.attention[2].earned, 5)

    def test_empty_attention_when_all_pass(self) -> None:
        checks = [
            _check("robots_txt", "scrapability", state="pass", earned=6, max_score=6),
            _check("bot_access", "scrapability", state="pass", earned=6, max_score=6),
            _check("html_structure", "scrapability", state="pass", earned=4, max_score=4),
            _check("schema_ld", "scrapability", state="pass", earned=5, max_score=5),
            _check("llms_txt", "scrapability", state="pass", earned=5, max_score=5),
            _check("ssr", "scrapability", state="pass", earned=4, max_score=4),
            _check("machine_surfaces", "scrapability", state="pass", earned=3, max_score=3),
            _check("pagespeed", "seo", state="pass", earned=3, max_score=3),
            _check("canonical", "seo", state="pass", earned=5, max_score=5),
            _check("indexing", "seo", state="pass", earned=5, max_score=5),
            _check("search_discovery", "seo", state="pass", earned=4, max_score=4),
            _check("social", "off_site", state="pass", earned=2, max_score=2),
            _check("wikipedia", "off_site", state="pass", earned=4, max_score=4),
        ]
        result = _build_result(
            include_protocols=False,
            include_account_auth=False,
            include_ecommerce=False,
            checks=checks,
        )
        summary = build_report_summary(result)
        self.assertEqual(summary.attention, [])
        self.assertEqual(summary.checks.attention_total, 0)
        self.assertEqual(summary.checks.fail, 0)
        self.assertEqual(summary.checks.partial, 0)
        self.assertEqual(summary.checks.warn, 0)
        # ``pass`` is a reserved Python keyword; the model exposes the
        # public field under ``pass_count`` while keeping the JSON key
        # ``pass`` via Pydantic aliasing.
        self.assertEqual(summary.checks.pass_count, 13)
        self.assertEqual(summary.checks.locked, 9)
        self.assertEqual(summary.checks.included, 13)


class PrivateFieldBoundaryTests(unittest.TestCase):
    def test_forbidden_keys_absent_from_payload(self) -> None:
        result = _build_result(
            include_protocols=False,
            include_account_auth=False,
            include_ecommerce=False,
        )
        summary = build_report_summary(result)
        payload = summary.model_dump(mode="json")

        def _walk(node: object) -> None:
            if isinstance(node, dict):
                for key, child in node.items():
                    self.assertNotIn(
                        key,
                        {
                            "finding",
                            "fix",
                            "label",
                            "entries",
                            "nearest",
                            "basis",
                            "caveat",
                            "passed",
                            "missing",
                            "not_checked",
                            "display_label",
                            "context",
                            "raw",
                            "rate_limit",
                        },
                        f"forbidden key {key!r} leaked into summary payload",
                    )
                    _walk(child)
            elif isinstance(node, list):
                for child in node:
                    _walk(child)

        _walk(payload)
        # Top-level scope must be SummaryScope, not full AuditScope.
        self.assertEqual(
            set(summary.scope.model_dump(mode="json")),
            {"preset", "protocols", "account_auth", "ecommerce", "overrides"},
        )
        # Attention rows must only contain the public summary keys.
        if summary.attention:
            allowed = {
                "check_name",
                "pillar",
                "state",
                "evidence_level",
                "earned",
                "max",
                "effort",
            }
            self.assertEqual(
                set(summary.attention[0].model_dump(mode="json")),
                allowed,
            )


class LimitationCodesTests(unittest.TestCase):
    def test_limitation_codes_fixed_order(self) -> None:
        for include_protocols, include_account_auth, include_ecommerce in product(
            (False, True), repeat=3
        ):
            with self.subTest(
                p=include_protocols,
                a=include_account_auth,
                c=include_ecommerce,
            ):
                result = _build_result(
                    include_protocols=include_protocols,
                    include_account_auth=include_account_auth,
                    include_ecommerce=include_ecommerce,
                )
                summary = build_report_summary(result)
                self.assertEqual(
                    summary.limitations,
                    (
                        "relative_scores",
                        "no_live_ranking",
                        "no_provider_ip_auth",
                        "no_paid_crawlers",
                    ),
                )


class AgentPercentTests(unittest.TestCase):
    def test_agent_percent_taken_from_result_score(self) -> None:
        result = _build_result(
            include_protocols=True,
            include_account_auth=True,
            include_ecommerce=True,
        )
        # Replace the agent readiness summary with a custom percentage.
        result.agent_readiness = _agent_readiness(
            include_protocols=True,
            include_account_auth=True,
            include_ecommerce=True,
            score=64,
            earned=12,
        )
        summary = build_report_summary(result)
        self.assertEqual(summary.scores.agent_readiness.percent, 64)
        self.assertEqual(summary.scores.agent_readiness.earned, 12)
        self.assertEqual(summary.scores.agent_readiness.max, 21)


class ScopeVariantTests(unittest.TestCase):
    def test_all_eight_scope_variants_produce_correct_max(self) -> None:
        expected = {
            (False, False, False): 8,
            (True, False, False): 14,
            (False, True, False): 11,
            (False, False, True): 12,
            (True, True, False): 17,
            (True, False, True): 18,
            (False, True, True): 15,
            (True, True, True): 21,
        }
        for (
            include_protocols,
            include_account_auth,
            include_ecommerce,
        ), strict_max in expected.items():
            with self.subTest(
                p=include_protocols,
                a=include_account_auth,
                c=include_ecommerce,
            ):
                result = _build_result(
                    include_protocols=include_protocols,
                    include_account_auth=include_account_auth,
                    include_ecommerce=include_ecommerce,
                )
                summary = build_report_summary(result)
                self.assertEqual(
                    summary.scores.agent_readiness.max,
                    strict_max,
                    f"max mismatch for (p={include_protocols}, "
                    f"a={include_account_auth}, c={include_ecommerce})",
                )
                # 13 groups, 56 checked points, 30/40/30 pillars must hold.
                self.assertEqual(summary.checks.included, 13)
                self.assertEqual(summary.checks.locked, 9)
                self.assertEqual(summary.scores.essentials.max, 56)
                self.assertEqual(summary.scores.pillars["off_site"].max, 30)
                self.assertEqual(summary.scores.pillars["scrapability"].max, 40)
                self.assertEqual(summary.scores.pillars["seo"].max, 30)
                self.assertEqual(summary.scores.overall.max, 100)


class LockedRowsTests(unittest.TestCase):
    def test_locked_rows_excluded_from_attention(self) -> None:
        checks = [
            _check("robots_txt", "scrapability", state="fail", earned=0, max_score=6),
            _check("bot_access", "scrapability", state="pass", earned=6, max_score=6),
            _check("html_structure", "scrapability", state="pass", earned=4, max_score=4),
            _check("schema_ld", "scrapability", state="pass", earned=5, max_score=5),
            _check("llms_txt", "scrapability", state="pass", earned=5, max_score=5),
            _check("ssr", "scrapability", state="pass", earned=4, max_score=4),
            _check("machine_surfaces", "scrapability", state="pass", earned=3, max_score=3),
            _check("pagespeed", "seo", state="pass", earned=3, max_score=3),
            _check("canonical", "seo", state="pass", earned=5, max_score=5),
            _check("indexing", "seo", state="pass", earned=5, max_score=5),
            _check("search_discovery", "seo", state="pass", earned=4, max_score=4),
            _check("social", "off_site", state="pass", earned=2, max_score=2),
            _check("wikipedia", "off_site", state="pass", earned=4, max_score=4),
        ]
        result = _build_result(
            include_protocols=False,
            include_account_auth=False,
            include_ecommerce=False,
            checks=checks,
        )
        summary = build_report_summary(result)
        # Only one row should be in attention: robots_txt is the lone fail.
        self.assertEqual(len(summary.attention), 1)
        self.assertEqual(summary.attention[0].check_name, "robots_txt")
        # 9 locked rows must be counted separately and never appear in attention.
        self.assertEqual(summary.checks.locked, 9)
        self.assertEqual(summary.checks.included, 13)
        self.assertNotIn("locked", [row.state for row in summary.attention])


class TieBreakerTests(unittest.TestCase):
    def test_same_state_same_ratio_breaks_tie_on_check_name(self) -> None:
        checks = [
            _check("schema_ld", "scrapability", state="fail", earned=1, max_score=5),
            _check("llms_txt", "scrapability", state="fail", earned=1, max_score=5),
            _check("robots_txt", "scrapability", state="pass", earned=6, max_score=6),
            _check("bot_access", "scrapability", state="pass", earned=6, max_score=6),
            _check("html_structure", "scrapability", state="pass", earned=4, max_score=4),
            _check("ssr", "scrapability", state="pass", earned=4, max_score=4),
            _check("machine_surfaces", "scrapability", state="pass", earned=3, max_score=3),
            _check("pagespeed", "seo", state="pass", earned=3, max_score=3),
            _check("canonical", "seo", state="pass", earned=5, max_score=5),
            _check("indexing", "seo", state="pass", earned=5, max_score=5),
            _check("search_discovery", "seo", state="pass", earned=4, max_score=4),
            _check("social", "off_site", state="pass", earned=2, max_score=2),
            _check("wikipedia", "off_site", state="pass", earned=4, max_score=4),
        ]
        result = _build_result(
            include_protocols=False,
            include_account_auth=False,
            include_ecommerce=False,
            checks=checks,
        )
        summary = build_report_summary(result)
        names = [row.check_name for row in summary.attention]
        # Same state, same ratio; alphabetical order wins.
        self.assertEqual(names, ["llms_txt", "schema_ld"])

    def test_max_zero_rows_sort_first_within_severity(self) -> None:
        checks = [
            _check("robots_txt", "scrapability", state="fail", earned=0, max_score=6),
            _check("bot_access", "scrapability", state="fail", earned=0, max_score=6),
            _check("html_structure", "scrapability", state="fail", earned=0, max_score=0),
            _check("schema_ld", "scrapability", state="pass", earned=5, max_score=5),
            _check("llms_txt", "scrapability", state="pass", earned=5, max_score=5),
            _check("ssr", "scrapability", state="pass", earned=4, max_score=4),
            _check("machine_surfaces", "scrapability", state="pass", earned=3, max_score=3),
            _check("pagespeed", "seo", state="pass", earned=3, max_score=3),
            _check("canonical", "seo", state="pass", earned=5, max_score=5),
            _check("indexing", "seo", state="pass", earned=5, max_score=5),
            _check("search_discovery", "seo", state="pass", earned=4, max_score=4),
            _check("social", "off_site", state="pass", earned=2, max_score=2),
            _check("wikipedia", "off_site", state="pass", earned=4, max_score=4),
        ]
        result = _build_result(
            include_protocols=False,
            include_account_auth=False,
            include_ecommerce=False,
            checks=checks,
        )
        summary = build_report_summary(result)
        # All three fail rows have ratio 0 (max=0 forces the floor).
        # Stable tie-break is alphabetical on check_name.
        names = [row.check_name for row in summary.attention]
        self.assertEqual(names, ["bot_access", "html_structure", "robots_txt"])
        # The max=0 row is part of the attention set; the field reports 0
        # for ``max`` so an agent can detect the unsalvageable row.
        self.assertEqual(summary.attention[1].max, 0)


if __name__ == "__main__":
    unittest.main()
