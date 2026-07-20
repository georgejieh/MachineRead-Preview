"""Preset pipeline integration tests.

End-to-end coverage that proves every one of the seven presets works through
the real FastAPI /audit pipeline, lands on the expected benchmark scope key,
and produces the matching strict-agent-readiness maximum. Also exercises the
two backward-compatibility paths the chunk promises:

- Legacy callers sending only the three ``include_*`` booleans keep working.
- A preset always wins over a conflicting ``include_*`` value, so the report
  reflects the preset the user picked rather than the leftover legacy flags.

The checks, agent-readiness summary, and benchmark assembly are all mocked so
the test stays hermetic and fast; the assertions focus on the scope metadata
that the preset model owns and the audit response contract must preserve.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import benchmarks  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    AgentBenchmarkComparison,
    AgentReadinessCategory,
    AgentReadinessSummary,
    CheckResult,
)
from app.presets import (  # noqa: E402
    PRESETS,
    benchmark_scope_key_for_scope,
    resolve_scope,
)


# (preset_key, expected_scope_key, expected_agent_max)
# These pairs are derived from the preset definitions. The expected scope key
# matches the documented 8-variant benchmark contract; the expected agent
# max matches ``_agent_max_for_scope`` in ``backend/app/benchmarks.py``.
EXPECTED_PRESET_SCOPE: dict[str, tuple[str, int]] = {
    "blog": ("p0_a0_c0", 8),
    "corporate": ("p0_a0_c0", 8),
    "services": ("p0_a0_c0", 8),
    "ecommerce": ("p1_a1_c1", 21),
    "news": ("p0_a0_c0", 8),
    "saas": ("p1_a1_c0", 17),
    # Custom defaults to the Blog/Content base.
    "custom": ("p0_a0_c0", 8),
}


def _stub_check() -> CheckResult:
    return CheckResult(
        pillar="scrapability",
        check_name="preset_probe",
        label="Preset integration probe",
        state="partial",
        evidence_level="inferred",
        available_in="Essentials preset fixture",
        score=3,
        max_score=5,
        finding="Stub evidence for preset pipeline integration.",
        fix="Replace stub evidence with real fixture data.",
        effort="medium",
    )


def _agent_readiness(max_value: int) -> AgentReadinessSummary:
    return AgentReadinessSummary(
        score=35,
        earned=7,
        max=max_value,
        label="Developing agent-native readiness",
        categories=[
            AgentReadinessCategory(
                name="Preset category",
                earned=2,
                max=5,
                score=40,
                passed=["Public discovery signal"],
                missing=["Public protocol signal"],
                excluded=["No excluded preset signals"],
            )
        ],
        passed=["Public discovery signal"],
        missing=["Public protocol signal"],
        not_checked=["Live agent journey"],
        benchmark=AgentBenchmarkComparison(
            score=35,
            earned=7,
            max=max_value,
            benchmark_count=0,
            median_score=0,
            percentile=100,
            position_label="Preset fixture position",
            nearest=[],
            entries=[],
            basis="Preset fixture agent benchmark basis",
            snapshot_date="2026-07-18",
            caveat="Preset fixture agent benchmark caveat.",
        ),
        caveat="Preset fixture readiness caveat.",
    )


class PresetPipelineIntegrationTests(unittest.TestCase):
    """Every preset resolves through the real /audit endpoint and lands on
    the expected benchmark scope + strict-agent-readiness maximum."""

    def _post_audit(self, body: dict) -> tuple[int, dict]:
        normalized_url = "https://preset.example/"
        with (
            patch("app.main.validate_url", return_value=normalized_url),
            patch(
                "app.main._build_context_or_error",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "app.main._run_essential_checks",
                new=AsyncMock(return_value=[_stub_check()]),
            ),
            patch(
                "app.main._safe_agent_readiness_summary",
                new=AsyncMock(side_effect=lambda ctx, p, a, c, **_: _agent_readiness(
                    benchmarks._agent_max_for_scope(p, a, c)
                )),
            ),
        ):
            response = TestClient(app).post("/audit", json=body)
        return response.status_code, response.json()

    def test_all_seven_presets_return_200(self) -> None:
        for preset_key in PRESETS:
            with self.subTest(preset=preset_key):
                status, _ = self._post_audit({"url": "preset.example", "preset": preset_key})
                self.assertEqual(status, 200, f"preset={preset_key} returned {status}")

    def test_each_preset_sets_expected_benchmark_scope_key(self) -> None:
        # The backend exposes the benchmark scope key indirectly through the
        # scope booleans. We re-derive the canonical key from those booleans
        # and compare it against the expected preset map, so a future change
        # that drifts a preset away from its documented scope key will fail.
        for preset_key, (expected_key, _expected_max) in EXPECTED_PRESET_SCOPE.items():
            with self.subTest(preset=preset_key):
                _status, payload = self._post_audit(
                    {"url": "preset.example", "preset": preset_key}
                )
                scope = payload["scope"]
                derived_key = (
                    f"p{int(scope['include_protocols'])}_"
                    f"a{int(scope['include_account_auth'])}_"
                    f"c{int(scope['include_ecommerce'])}"
                )
                self.assertEqual(
                    derived_key,
                    expected_key,
                    f"preset={preset_key} expected benchmark key "
                    f"{expected_key!r}, got {derived_key!r}",
                )

    def test_each_preset_sets_expected_strict_agent_max(self) -> None:
        for preset_key, (_expected_key, expected_max) in EXPECTED_PRESET_SCOPE.items():
            with self.subTest(preset=preset_key):
                _status, payload = self._post_audit(
                    {"url": "preset.example", "preset": preset_key}
                )
                agent_max = payload["agent_readiness"]["max"]
                self.assertEqual(
                    agent_max,
                    expected_max,
                    f"preset={preset_key} expected strict-agent max "
                    f"{expected_max}, got {agent_max}",
                )

    def test_each_preset_sets_preset_applied_field(self) -> None:
        for preset_key in PRESETS:
            with self.subTest(preset=preset_key):
                _status, payload = self._post_audit(
                    {"url": "preset.example", "preset": preset_key}
                )
                self.assertEqual(
                    payload["scope"]["preset_applied"],
                    preset_key,
                )
                self.assertEqual(
                    payload["scope"]["machine_surfaces_scope"],
                    resolve_scope(preset_key, None).machine_surfaces,
                )

    def test_scope_label_uses_preset_definition_label(self) -> None:
        for preset_key, scope in resolve_scope.__globals__["PRESETS"].items():
            with self.subTest(preset=preset_key):
                _status, payload = self._post_audit(
                    {"url": "preset.example", "preset": preset_key}
                )
                self.assertEqual(
                    payload["scope"]["label"],
                    scope.label,
                )


class LegacyBackwardCompatTests(unittest.TestCase):
    """The legacy three-boolean path keeps working unchanged."""

    def _post_audit(self, body: dict) -> tuple[int, dict]:
        normalized_url = "https://legacy.example/"
        with (
            patch("app.main.validate_url", return_value=normalized_url),
            patch(
                "app.main._build_context_or_error",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "app.main._run_essential_checks",
                new=AsyncMock(return_value=[_stub_check()]),
            ),
            patch(
                "app.main._safe_agent_readiness_summary",
                new=AsyncMock(side_effect=lambda ctx, p, a, c, **_: _agent_readiness(
                    benchmarks._agent_max_for_scope(p, a, c)
                )),
            ),
        ):
            response = TestClient(app).post("/audit", json=body)
        return response.status_code, response.json()

    def test_no_preset_no_overrides_uses_default_general_scope(self) -> None:
        status, payload = self._post_audit({"url": "legacy.example"})
        self.assertEqual(status, 200)
        scope = payload["scope"]
        self.assertIsNone(scope["preset_applied"])
        self.assertEqual(scope["overrides_applied"], {})
        self.assertEqual(scope["include_protocols"], False)
        self.assertEqual(scope["include_account_auth"], False)
        self.assertEqual(scope["include_ecommerce"], False)
        self.assertEqual(payload["agent_readiness"]["max"], 8)
        self.assertEqual(scope["machine_surfaces_scope"], "common-contextual")

    def test_legacy_booleans_only_passes_with_true_flags(self) -> None:
        status, payload = self._post_audit(
            {
                "url": "legacy.example",
                "include_protocols": True,
                "include_account_auth": False,
                "include_ecommerce": True,
            }
        )
        self.assertEqual(status, 200)
        scope = payload["scope"]
        self.assertIsNone(scope["preset_applied"])
        self.assertEqual(scope["include_protocols"], True)
        self.assertEqual(scope["include_account_auth"], False)
        self.assertEqual(scope["include_ecommerce"], True)
        self.assertEqual(payload["agent_readiness"]["max"], 8 + 6 + 4)

    def test_legacy_full_scope_matches_eight_variant_max(self) -> None:
        status, payload = self._post_audit(
            {
                "url": "legacy.example",
                "include_protocols": True,
                "include_account_auth": True,
                "include_ecommerce": True,
            }
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["agent_readiness"]["max"], 21)
        scope = payload["scope"]
        self.assertEqual(
            (
                f"p{int(scope['include_protocols'])}_"
                f"a{int(scope['include_account_auth'])}_"
                f"c{int(scope['include_ecommerce'])}"
            ),
            "p1_a1_c1",
        )


class PresetOverridesLegacyTests(unittest.TestCase):
    """When preset and legacy booleans disagree, the preset wins."""

    def _post_audit(self, body: dict) -> tuple[int, dict]:
        normalized_url = "https://override.example/"
        with (
            patch("app.main.validate_url", return_value=normalized_url),
            patch(
                "app.main._build_context_or_error",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "app.main._run_essential_checks",
                new=AsyncMock(return_value=[_stub_check()]),
            ),
            patch(
                "app.main._safe_agent_readiness_summary",
                new=AsyncMock(side_effect=lambda ctx, p, a, c, **_: _agent_readiness(
                    benchmarks._agent_max_for_scope(p, a, c)
                )),
            ),
        ):
            response = TestClient(app).post("/audit", json=body)
        return response.status_code, response.json()

    def test_ecommerce_preset_overrides_include_ecommerce_false(self) -> None:
        status, payload = self._post_audit(
            {
                "url": "override.example",
                "preset": "ecommerce",
                "include_ecommerce": False,
            }
        )
        self.assertEqual(status, 200)
        scope = payload["scope"]
        self.assertEqual(scope["preset_applied"], "ecommerce")
        # The preset wins: ecommerce scope must stay on even though the legacy
        # boolean tried to disable it.
        self.assertTrue(scope["include_ecommerce"])
        self.assertEqual(payload["agent_readiness"]["max"], 21)
        self.assertEqual(
            (
                f"p{int(scope['include_protocols'])}_"
                f"a{int(scope['include_account_auth'])}_"
                f"c{int(scope['include_ecommerce'])}"
            ),
            "p1_a1_c1",
        )

    def test_blog_preset_overrides_all_legacy_booleans_true(self) -> None:
        # Caller asks for every dimension on, but the Blog/Content preset must
        # still resolve to p0_a0_c0 because the preset wins over the legacy
        # booleans.
        status, payload = self._post_audit(
            {
                "url": "override.example",
                "preset": "blog",
                "include_protocols": True,
                "include_account_auth": True,
                "include_ecommerce": True,
            }
        )
        self.assertEqual(status, 200)
        scope = payload["scope"]
        self.assertEqual(scope["preset_applied"], "blog")
        self.assertFalse(scope["include_protocols"])
        self.assertFalse(scope["include_account_auth"])
        self.assertFalse(scope["include_ecommerce"])
        self.assertEqual(payload["agent_readiness"]["max"], 8)

    def test_custom_preset_with_no_overrides_matches_legacy_default(self) -> None:
        # Custom starts from the same default as Blog; with no overrides it
        # should produce the same scope metadata as the legacy default path.
        status, payload = self._post_audit(
            {
                "url": "override.example",
                "preset": "custom",
            }
        )
        self.assertEqual(status, 200)
        scope = payload["scope"]
        self.assertEqual(scope["preset_applied"], "custom")
        self.assertFalse(scope["include_protocols"])
        self.assertFalse(scope["include_account_auth"])
        self.assertFalse(scope["include_ecommerce"])
        self.assertEqual(payload["agent_readiness"]["max"], 8)
        self.assertEqual(scope["machine_surfaces_scope"], "common-contextual")

    def test_custom_preset_with_protocol_override_extends_scope(self) -> None:
        status, payload = self._post_audit(
            {
                "url": "override.example",
                "preset": "custom",
                "custom_overrides": {"protocols": True, "api_catalog": True},
            }
        )
        self.assertEqual(status, 200)
        scope = payload["scope"]
        self.assertEqual(scope["preset_applied"], "custom")
        self.assertTrue(scope["include_protocols"])
        self.assertEqual(scope["overrides_applied"]["protocols"], True)
        self.assertEqual(scope["overrides_applied"]["api_catalog"], True)
        self.assertEqual(payload["agent_readiness"]["max"], 8 + 6)
        self.assertEqual(scope["machine_surfaces_scope"], "full")


class PresetContractIntegrationTests(unittest.TestCase):
    """Cross-check the pipeline's reported metadata against the standalone
    ``resolve_scope`` helper, so the two stay in sync."""

    def test_response_metadata_matches_resolve_scope(self) -> None:
        normalized_url = "https://mirror.example/"
        for preset_key in PRESETS:
            with (
                self.subTest(preset=preset_key),
                patch("app.main.validate_url", return_value=normalized_url),
                patch(
                    "app.main._build_context_or_error",
                    new=AsyncMock(return_value=object()),
                ),
                patch(
                    "app.main._run_essential_checks",
                    new=AsyncMock(return_value=[_stub_check()]),
                ),
                patch(
                    "app.main._safe_agent_readiness_summary",
                    new=AsyncMock(side_effect=lambda ctx, p, a, c, **_: _agent_readiness(
                        benchmarks._agent_max_for_scope(p, a, c)
                    )),
                ),
            ):
                response = TestClient(app).post(
                    "/audit", json={"url": "mirror.example", "preset": preset_key}
                )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            scope = payload["scope"]
            resolved = resolve_scope(preset_key, None)
            self.assertEqual(scope["include_protocols"], resolved.include_protocols)
            self.assertEqual(scope["include_account_auth"], resolved.include_account_auth)
            self.assertEqual(scope["include_ecommerce"], resolved.include_ecommerce)
            self.assertEqual(scope["machine_surfaces_scope"], resolved.machine_surfaces)
            self.assertEqual(
                scope["included_families"],
                list(resolved.included_families),
            )
            self.assertEqual(
                scope["excluded_families"],
                list(resolved.excluded_families),
            )
            self.assertEqual(
                benchmark_scope_key_for_scope(resolved),
                (
                    f"p{int(scope['include_protocols'])}_"
                    f"a{int(scope['include_account_auth'])}_"
                    f"c{int(scope['include_ecommerce'])}"
                ),
            )


if __name__ == "__main__":
    unittest.main()
