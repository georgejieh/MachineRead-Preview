import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import benchmarks
from app.benchmarks import (
    SCHEMA_VERSION,
    BenchmarkProfile,
    peer_agent,
    peer_essentials,
)
from app.models import CheckResult
from app.rubric import ESSENTIALS_CHECK_GROUPS, ESSENTIALS_CHECKED_MAX


# --- Helpers --------------------------------------------------------------

def _all_check_names() -> set[str]:
    return {g.check_name for g in ESSENTIALS_CHECK_GROUPS}


def _v2_profile_payload(*, peer_name: str = "Fixture Peer", score: int = 1) -> dict:
    """Build a minimal-but-valid v2 profile dict for tests."""
    checks: dict[str, dict] = {}
    for g in ESSENTIALS_CHECK_GROUPS:
        checks[g.check_name] = {
            "score": score,
            "max": g.max_score,
            "state": "partial" if score > 0 else "fail",
            "evidence_level": "verified",
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_date": "2026-07-20",
        "profiles": [
            {
                "name": peer_name,
                "category": "Fixture category",
                "group": "fixture",
                "size": "test",
                "url": "https://fixture.example",
                "essentials_checks": checks,
                "agent_passed": [],
            }
        ],
    }


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


# --- Existing fixture-based tests (rewritten for v2) ---------------------

class BenchmarkFixtureTests(unittest.TestCase):
    def test_sample_profiles_load_under_v2(self) -> None:
        # Sample seeds must parse, validate, and produce a non-empty profile set.
        self.assertGreaterEqual(len(benchmarks._BENCHMARK_PROFILES), 12)
        for profile in benchmarks._BENCHMARK_PROFILES:
            self.assertIn("essentials_checks", profile)
            self.assertIn("agent_passed", profile)
            self.assertEqual(
                set(profile["essentials_checks"].keys()),
                _all_check_names(),
            )

    def test_configured_profile_path_loads_fixture_without_private_data(self) -> None:
        payload = _v2_profile_payload()
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = Path(temp_dir) / "benchmark_profiles.json"
            profile_path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.dict(os.environ, {"MACHINEREAD_BENCHMARK_PROFILE_PATH": str(profile_path)}):
                with patch.object(benchmarks, "_DEFAULT_BENCHMARK_PROFILE_PATH", Path(temp_dir) / "unused.json"):
                    loaded_profiles = benchmarks._load_benchmark_profiles()

        self.assertEqual(len(loaded_profiles), 1)
        self.assertEqual(loaded_profiles[0]["name"], "Fixture Peer")
        # Snapshot is immutable after load (no list / dict values to mutate)
        self.assertIsInstance(loaded_profiles[0]["essentials_checks"], dict)
        self.assertIsInstance(loaded_profiles[0]["agent_passed"], tuple)

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

    def test_essentials_benchmark_returns_defaults_when_scope_has_no_peers(self) -> None:
        with (
            patch.object(benchmarks, "_entries", return_value=[]),
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
            patch.object(benchmarks, "_entries", return_value=[]),
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


# --- Per-check behaviour tests -------------------------------------------

class PeerProfileBehaviorTests(unittest.TestCase):
    def test_peer_agent_differs_across_scopes(self) -> None:
        profile = {
            "name": "Synthetic Scope Peer",
            "category": "test",
            "group": "test",
            "size": "test",
            "url": "https://scope.example",
            "essentials_checks": {},
            "agent_passed": [
                "robots.txt published",
                "valid sitemap discovery",
                "API Catalog",
                "MCP Server Card",
            ],
        }

        base_earned, base_max = peer_agent(profile, False, False, False)
        protocols_earned, protocols_max = peer_agent(profile, True, False, False)
        full_earned, full_max = peer_agent(profile, True, True, True)

        self.assertEqual(base_max, 8)
        self.assertEqual(base_earned, 2)

        self.assertEqual(protocols_max, 14)
        self.assertEqual(protocols_earned, base_earned + 2)

        self.assertEqual(full_max, 21)
        self.assertEqual(full_earned, protocols_earned)

    def test_peer_essentials_excludes_unknown_evidence(self) -> None:
        # Build a profile where one check has evidence_level=unknown.
        # The same peer with all rows verified should have a strictly
        # larger checked_max.
        checks_verified: dict[str, dict] = {}
        checks_with_unknown: dict[str, dict] = {}
        for g in ESSENTIALS_CHECK_GROUPS:
            checks_verified[g.check_name] = {
                "score": g.max_score,
                "max": g.max_score,
                "state": "pass",
                "evidence_level": "verified",
            }
            level = "verified" if g.check_name != "wikipedia" else "unknown"
            checks_with_unknown[g.check_name] = {
                "score": 0,
                "max": g.max_score,
                "state": "warn",
                "evidence_level": level,
            }

        profile_verified = {
            "name": "Verified Peer",
            "category": "test",
            "group": "test",
            "size": "test",
            "url": "https://verified.example",
            "essentials_checks": checks_verified,
            "agent_passed": [],
        }
        profile_unknown = {
            "name": "Unknown Peer",
            "category": "test",
            "group": "test",
            "size": "test",
            "url": "https://unknown.example",
            "essentials_checks": checks_with_unknown,
            "agent_passed": [],
        }

        _, max_verified, _ = peer_essentials(profile_verified)
        _, max_unknown, _ = peer_essentials(profile_unknown)

        # The "wikipedia" row (4 points) is excluded from the unknown
        # peer's denominator; verified peer keeps the full ESSENTIALS_CHECKED_MAX.
        self.assertEqual(max_verified, ESSENTIALS_CHECKED_MAX)
        self.assertEqual(max_unknown, ESSENTIALS_CHECKED_MAX - 4)

    def test_peer_agent_ignores_unknown_probe_labels(self) -> None:
        # Profile's agent_passed includes a label that no longer exists in
        # the current scope probe list. The peer_agent helper must drop it
        # silently with no error and no contribution.
        profile = {
            "name": "Stale Peer",
            "category": "test",
            "group": "test",
            "size": "test",
            "url": "https://stale.example",
            "essentials_checks": {},
            "agent_passed": [
                "this probe no longer exists",
                "robots.txt published",  # one real label
            ],
        }
        # Even though agent_passed has 2 entries, only 1 is a real probe.
        earned, maximum = peer_agent(profile, False, False, False)
        self.assertEqual(earned, 1)
        self.assertEqual(maximum, 8)  # base scope

    def test_peer_agent_probe_outside_scope_contributes_zero(self) -> None:
        # Peer passes "API Catalog" (a protocol-scope probe) but the user
        # is at base scope → that probe is not in the scope's probe list
        # and contributes 0 to earned.
        profile = {
            "name": "Protocol Peer",
            "category": "test",
            "group": "test",
            "size": "test",
            "url": "https://protocol.example",
            "essentials_checks": {},
            "agent_passed": ["API Catalog", "MCP Server Card", "A2A Agent Card"],
        }
        # At base scope, no protocol probes are included
        earned, maximum = peer_agent(profile, False, False, False)
        self.assertEqual(earned, 0)
        # At full scope, those protocol passes count
        earned_full, maximum_full = peer_agent(profile, True, False, False)
        self.assertGreater(earned_full, 0)
        self.assertGreater(maximum_full, maximum)


# --- Validation tests (eager, at load) -----------------------------------

class BenchmarkValidationTests(unittest.TestCase):
    def _write_profile(self, profiles: list, snapshot_date: str = "2026-07-20") -> Path:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "snapshot_date": snapshot_date,
            "profiles": profiles,
        }
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        return tmp

    def test_v1_bare_list_raises_migration_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            v1_path = Path(temp_dir) / "v1.json"
            v1_path.write_text(json.dumps([{"name": "X"}]), encoding="utf-8")
            with patch.dict(os.environ, {"MACHINEREAD_BENCHMARK_PROFILE_PATH": str(v1_path)}):
                with self.assertRaises(ValueError) as ctx:
                    benchmarks._load_benchmark_profiles()
            self.assertIn("refresh_benchmarks.py", str(ctx.exception))

    def test_unknown_check_key_raises_at_load(self) -> None:
        base = _v2_profile_payload()["profiles"][0]
        base["essentials_checks"]["made_up_check"] = {
            "score": 0,
            "max": 1,
            "state": "fail",
            "evidence_level": "verified",
        }
        path = self._write_profile([base])
        with patch.dict(os.environ, {"MACHINEREAD_BENCHMARK_PROFILE_PATH": str(path)}):
            with self.assertRaises(ValueError) as ctx:
                benchmarks._load_benchmark_profiles()
        self.assertIn("made_up_check", str(ctx.exception))

    def test_score_exceeds_max_raises_at_load(self) -> None:
        base = _v2_profile_payload()["profiles"][0]
        first_check = next(iter(base["essentials_checks"]))
        base["essentials_checks"][first_check]["score"] = (
            base["essentials_checks"][first_check]["max"] + 1
        )
        path = self._write_profile([base])
        with patch.dict(os.environ, {"MACHINEREAD_BENCHMARK_PROFILE_PATH": str(path)}):
            with self.assertRaises(ValueError):
                benchmarks._load_benchmark_profiles()

    def test_bad_state_value_raises_at_load(self) -> None:
        base = _v2_profile_payload()["profiles"][0]
        first_check = next(iter(base["essentials_checks"]))
        base["essentials_checks"][first_check]["state"] = "nope"
        path = self._write_profile([base])
        with patch.dict(os.environ, {"MACHINEREAD_BENCHMARK_PROFILE_PATH": str(path)}):
            with self.assertRaises(ValueError) as ctx:
                benchmarks._load_benchmark_profiles()
        self.assertIn("nope", str(ctx.exception))

    def test_bad_evidence_level_raises_at_load(self) -> None:
        base = _v2_profile_payload()["profiles"][0]
        first_check = next(iter(base["essentials_checks"]))
        base["essentials_checks"][first_check]["evidence_level"] = "maybe"
        path = self._write_profile([base])
        with patch.dict(os.environ, {"MACHINEREAD_BENCHMARK_PROFILE_PATH": str(path)}):
            with self.assertRaises(ValueError) as ctx:
                benchmarks._load_benchmark_profiles()
        self.assertIn("maybe", str(ctx.exception))


# --- Refresh-script round-trip -------------------------------------------

class RefreshScriptRoundTripTests(unittest.TestCase):
    def test_refresh_script_round_trips_through_loader(self) -> None:
        # Build a fake profile dict the refresh script would emit.
        payload = _v2_profile_payload(peer_name="Round Trip Peer", score=2)
        with tempfile.TemporaryDirectory() as temp_dir:
            peers_path = Path(temp_dir) / "peers.json"
            peers_path.write_text(
                json.dumps([{"name": "Round Trip Peer", "category": "x",
                            "group": "y", "size": "z", "url": "https://rt.example"}]),
                encoding="utf-8",
            )
            out_path = Path(temp_dir) / "out.json"

            import importlib
            import scripts.refresh_benchmarks as refresh  # type: ignore

            # Patch _audit_one to return a v2 profile without HTTP.
            async def fake_audit_one(peer):
                return payload["profiles"][0]

            with patch.object(refresh, "_audit_one", side_effect=fake_audit_one):
                rc = refresh.main() if False else None
                # main() reads argv; instead call _run with explicit args.
                import argparse
                args = argparse.Namespace(
                    peers=peers_path, out=out_path, concurrency=1
                )
                rc = asyncio_run(refresh._run(args))

            self.assertEqual(rc, 0)
            self.assertTrue(out_path.exists())
            on_disk = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["schema_version"], SCHEMA_VERSION)
            self.assertEqual(len(on_disk["profiles"]), 1)
            self.assertEqual(on_disk["profiles"][0]["name"], "Round Trip Peer")

            # Now round-trip through the loader
            with patch.dict(os.environ, {"MACHINEREAD_BENCHMARK_PROFILE_PATH": str(out_path)}):
                with patch.object(benchmarks, "_DEFAULT_BENCHMARK_PROFILE_PATH", Path(temp_dir) / "unused.json"):
                    loaded = benchmarks._load_benchmark_profiles()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["name"], "Round Trip Peer")


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


# --- Exclusion rule tests (kept from v1) ---------------------------------

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
