import json
import os
import sys
import tempfile
import unittest
from itertools import product
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import benchmarks
from app.models import CheckResult
from app.rubric import ESSENTIALS_CHECKED_MAX


def _variant_for_scope(
    include_protocols: bool,
    include_account_auth: bool,
    include_ecommerce: bool,
    *,
    agent_earned: int = 2,
) -> dict[str, object]:
    checked_score = 30
    agent_max = benchmarks._agent_max_for_scope(
        include_protocols,
        include_account_auth,
        include_ecommerce,
    )
    return {
        "overall_score": checked_score,
        "free_evidence_score": round((checked_score / ESSENTIALS_CHECKED_MAX) * 100),
        "checked_score": checked_score,
        "checked_max": ESSENTIALS_CHECKED_MAX,
        "agent_readiness_score": round((agent_earned / agent_max) * 100),
        "agent_readiness_earned": agent_earned,
        "agent_readiness_max": agent_max,
        "pillar_scores": {
            "off_site": 6,
            "scrapability": 18,
            "seo": 6,
        },
    }


def _profile_fixture(*, agent_earned: int = 2) -> list[benchmarks.BenchmarkProfile]:
    variants = {}
    for include_protocols, include_account_auth, include_ecommerce in product((False, True), repeat=3):
        variants[
            benchmarks.benchmark_scope_key(
                include_protocols,
                include_account_auth,
                include_ecommerce,
            )
        ] = _variant_for_scope(
            include_protocols,
            include_account_auth,
            include_ecommerce,
            agent_earned=agent_earned,
        )
    return [
        {
            "name": "Fixture Peer",
            "category": "Fixture category",
            "group": "fixture",
            "size": "test",
            "url": "https://fixture.example",
            "variants": variants,
        }
    ]


def _checks_fixture() -> list[CheckResult]:
    return [
        CheckResult(
            pillar="scrapability",
            check_name="html_structure",
            label="Semantic HTML",
            state="pass",
            score=28,
            max_score=40,
            finding="Readable HTML.",
            fix="No action needed.",
            effort="low",
        )
    ]


class BenchmarkFixtureTests(unittest.TestCase):
    def test_sample_profiles_cover_every_scope_without_private_data(self) -> None:
        expected_scope_keys = {
            benchmarks.benchmark_scope_key(
                include_protocols,
                include_account_auth,
                include_ecommerce,
            )
            for include_protocols, include_account_auth, include_ecommerce in product((False, True), repeat=3)
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            missing_default = Path(temp_dir) / "missing-private-profiles.json"
            with patch.dict(os.environ, {"MACHINEREAD_BENCHMARK_PROFILE_PATH": ""}):
                with patch.object(benchmarks, "_DEFAULT_BENCHMARK_PROFILE_PATH", missing_default):
                    profiles = benchmarks._load_benchmark_profiles()

        self.assertEqual(len(profiles), 14)
        for profile in profiles:
            self.assertEqual(set(profile["variants"]), expected_scope_keys)
            for scope_key, variant in profile["variants"].items():
                include_protocols, include_account_auth, include_ecommerce = benchmarks._scope_flags_from_key(scope_key)
                self.assertEqual(variant["checked_max"], ESSENTIALS_CHECKED_MAX)
                self.assertEqual(
                    variant["agent_readiness_max"],
                    benchmarks._agent_max_for_scope(
                        include_protocols,
                        include_account_auth,
                        include_ecommerce,
                    ),
                )

    def test_configured_profile_path_loads_fixture_without_private_data(self) -> None:
        profiles = _profile_fixture()

        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = Path(temp_dir) / "benchmark_profiles.json"
            profile_path.write_text(json.dumps(profiles), encoding="utf-8")
            with patch.dict(os.environ, {"MACHINEREAD_BENCHMARK_PROFILE_PATH": str(profile_path)}):
                with patch.object(benchmarks, "_DEFAULT_BENCHMARK_PROFILE_PATH", Path(temp_dir) / "unused.json"):
                    loaded_profiles = benchmarks._load_benchmark_profiles()

        self.assertEqual(loaded_profiles, profiles)

        with patch.object(benchmarks, "_BENCHMARK_PROFILES", loaded_profiles):
            comparison = benchmarks.build_benchmark_comparison(_checks_fixture())

        self.assertEqual(comparison.benchmark_count, 1)
        self.assertEqual(comparison.entries[0].name, "Fixture Peer")
        self.assertEqual(comparison.entries[0].checked_max, ESSENTIALS_CHECKED_MAX)

    def test_configured_missing_profile_path_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_profile_path = Path(temp_dir) / "missing.json"
            with patch.dict(os.environ, {"MACHINEREAD_BENCHMARK_PROFILE_PATH": str(missing_profile_path)}):
                with self.assertRaises(FileNotFoundError) as error_context:
                    benchmarks._load_benchmark_profiles()

        self.assertIn("Benchmark profile file not found", str(error_context.exception))

    def test_agent_benchmark_normalizes_loaded_profile_to_current_scope(self) -> None:
        profiles = _profile_fixture(agent_earned=99)

        with patch.object(benchmarks, "_BENCHMARK_PROFILES", profiles):
            comparison = benchmarks.build_agent_benchmark_comparison(
                score=25,
                earned=5,
                maximum=20,
                include_protocols=True,
                include_account_auth=True,
                include_ecommerce=True,
            )

        self.assertEqual(comparison.benchmark_count, 1)
        self.assertEqual(comparison.entries[0].agent_readiness_earned, 21)
        self.assertEqual(comparison.entries[0].agent_readiness_max, 21)
        self.assertEqual(comparison.entries[0].agent_readiness_score, 100)

    def test_essentials_benchmark_returns_defaults_when_scope_has_no_peers(self) -> None:
        with (
            patch.object(benchmarks, "_entries_for_scope", return_value=[]),
            patch.object(benchmarks, "_checked_points") as checked_points,
            patch.object(benchmarks, "median") as benchmark_median,
            patch.object(benchmarks, "_percentile") as percentile,
            patch.object(benchmarks, "_position_label") as position_label,
        ):
            comparison = benchmarks.build_benchmark_comparison(_checks_fixture())

        checked_points.assert_not_called()
        benchmark_median.assert_not_called()
        percentile.assert_not_called()
        position_label.assert_not_called()
        self.assertEqual(comparison.score, 0)
        self.assertEqual(comparison.checked_score, 0)
        self.assertEqual(comparison.checked_max, 0)
        self.assertEqual(comparison.benchmark_count, 0)
        self.assertEqual(comparison.median_score, 0)
        self.assertEqual(comparison.percentile, 0)
        self.assertEqual(comparison.position_label, "No peers available")
        self.assertEqual(comparison.nearest, [])
        self.assertEqual(comparison.entries, [])

    def test_agent_benchmark_returns_defaults_when_scope_has_no_peers(self) -> None:
        with (
            patch.object(benchmarks, "_entries_for_scope", return_value=[]),
            patch.object(benchmarks, "median") as benchmark_median,
            patch.object(benchmarks, "_agent_percentile") as percentile,
            patch.object(benchmarks, "_agent_position_label") as position_label,
        ):
            comparison = benchmarks.build_agent_benchmark_comparison(
                score=25,
                earned=2,
                maximum=8,
            )

        benchmark_median.assert_not_called()
        percentile.assert_not_called()
        position_label.assert_not_called()
        self.assertEqual(comparison.score, 0)
        self.assertEqual(comparison.earned, 0)
        self.assertEqual(comparison.max, 0)
        self.assertEqual(comparison.benchmark_count, 0)
        self.assertEqual(comparison.median_score, 0)
        self.assertEqual(comparison.percentile, 0)
        self.assertEqual(comparison.position_label, "No peers available")
        self.assertEqual(comparison.nearest, [])
        self.assertEqual(comparison.entries, [])


class CheckedPointsExclusionTests(unittest.TestCase):
    def test_warn_unknown_evidence_rows_excluded_from_denominator(self) -> None:
        # Two real scored rows + one warn-state fallback. The fallback carries
        # evidence_level='unknown' (per _fallback_check_result) and contributes
        # 0/group_max to the denominator; transient fetch failures must not
        # silently drag the Evidence score down. The denominator should reflect
        # only the rows the audit could actually evaluate.
        checks = [
            CheckResult(
                pillar="scrapability",
                check_name="html_structure",
                label="Semantic HTML",
                state="pass",
                score=8,
                max_score=10,
                finding="Readable HTML.",
                fix="No action needed.",
                effort="low",
            ),
            CheckResult(
                pillar="seo",
                check_name="sitemap",
                label="XML sitemap",
                state="partial",
                score=4,
                max_score=8,
                finding="Sitemap present.",
                fix="No action needed.",
                effort="low",
            ),
            CheckResult(
                pillar="off_site",
                check_name="wikipedia",
                label="Wikipedia entity",
                state="warn",
                evidence_level="unknown",
                score=0,
                max_score=6,
                finding=(
                    "MachineRead could not complete this check during the audit, "
                    "so this row is inconclusive."
                ),
                fix="Retry the audit. If this warning repeats, review server logs.",
                effort="medium",
            ),
        ]

        score, maximum = benchmarks._checked_points(checks)

        self.assertEqual(score, 12)
        # 10 + 8 = 18. The warn row's 6 points must NOT be in the denominator.
        self.assertEqual(maximum, 18)

    def test_warn_unknown_rows_excluded_from_free_evidence_score(self) -> None:
        # The end-to-end consequence: free_evidence_score is calculated purely
        # over the rows the audit actually evaluated. Same fixture as above.
        checks = [
            CheckResult(
                pillar="scrapability",
                check_name="html_structure",
                label="Semantic HTML",
                state="pass",
                score=8,
                max_score=10,
                finding="Readable HTML.",
                fix="No action needed.",
                effort="low",
            ),
            CheckResult(
                pillar="seo",
                check_name="sitemap",
                label="XML sitemap",
                state="partial",
                score=4,
                max_score=8,
                finding="Sitemap present.",
                fix="No action needed.",
                effort="low",
            ),
            CheckResult(
                pillar="off_site",
                check_name="wikipedia",
                label="Wikipedia entity",
                state="warn",
                evidence_level="unknown",
                score=0,
                max_score=6,
                finding=(
                    "MachineRead could not complete this check during the audit, "
                    "so this row is inconclusive."
                ),
                fix="Retry the audit. If this warning repeats, review server logs.",
                effort="medium",
            ),
        ]

        score = benchmarks.free_evidence_score(checks)

        # 12/18 = 66.67 -> 67. With the warn row kept (12/24 = 50), the score
        # would silently misrepresent the audit. After the fix, denominator
        # excludes warn rows so the score reflects what was actually evaluated.
        self.assertEqual(score, round((12 / 18) * 100))

    def test_locked_rows_still_excluded_from_denominator(self) -> None:
        # Regression guard: the existing locked-row exclusion must still hold
        # alongside the new warn-row exclusion.
        checks = [
            CheckResult(
                pillar="scrapability",
                check_name="html_structure",
                label="Semantic HTML",
                state="pass",
                score=5,
                max_score=10,
                finding="Readable HTML.",
                fix="No action needed.",
                effort="low",
            ),
            CheckResult(
                pillar="off_site",
                check_name="paid_reserved",
                label="Paid-only signal",
                state="locked",
                evidence_level="not_applicable",
                score=0,
                max_score=12,
                finding="Reserved for paid tier.",
                fix="Upgrade to evaluate.",
                effort="low",
            ),
        ]

        score, maximum = benchmarks._checked_points(checks)

        self.assertEqual(score, 5)
        self.assertEqual(maximum, 10)


if __name__ == "__main__":
    unittest.main()
