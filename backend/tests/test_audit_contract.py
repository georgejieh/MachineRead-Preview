import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ["MACHINEREAD_AUDIT_RATE_LIMIT"] = "1000/minute"

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app
from app.models import (
    AgentBenchmarkComparison,
    AgentReadinessCategory,
    AgentReadinessSummary,
    CheckResult,
)


def _contract_check() -> CheckResult:
    return CheckResult(
        pillar="scrapability",
        check_name="contract_probe",
        label="Distinctive contract probe",
        state="partial",
        evidence_level="inferred",
        available_in="Essentials contract fixture",
        score=3,
        max_score=5,
        finding="Public fixture evidence was found.",
        fix="Publish the remaining public fixture evidence.",
        effort="medium",
    )


def _agent_readiness() -> AgentReadinessSummary:
    return AgentReadinessSummary(
        # Full-scope strict-agent fixture: 8+6+3+4=21.
        score=35,
        earned=7,
        max=21,
        label="Developing agent-native readiness",
        categories=[
            AgentReadinessCategory(
                name="Contract category",
                earned=2,
                max=5,
                score=40,
                passed=["Public discovery signal"],
                missing=["Public protocol signal"],
                excluded=["No excluded fixture signals"],
            )
        ],
        passed=["Public discovery signal"],
        missing=["Public protocol signal"],
        not_checked=["Live agent journey"],
        benchmark=AgentBenchmarkComparison(
            score=35,
            earned=7,
            max=21,
            benchmark_count=0,
            median_score=0,
            percentile=100,
            position_label="Contract fixture position",
            nearest=[],
            entries=[],
            basis="Contract fixture agent benchmark basis",
            snapshot_date="2026-07-16",
            caveat="Contract fixture agent benchmark caveat.",
        ),
        caveat="Contract fixture readiness caveat.",
    )


class AuditContractTests(unittest.TestCase):
    def test_post_audit_serializes_complete_public_contract(self) -> None:
        normalized_url = "https://contract.example/"
        with (
            patch("app.main.validate_url", return_value=normalized_url),
            patch(
                "app.main._build_context_or_error",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "app.main._run_essential_checks",
                new=AsyncMock(return_value=[_contract_check()]),
            ),
            patch(
                "app.main._safe_agent_readiness_summary",
                new=AsyncMock(return_value=_agent_readiness()),
            ),
        ):
            response = TestClient(app).post(
                "/audit",
                json={
                    "url": "contract.example",
                    "include_protocols": True,
                    "include_account_auth": True,
                    "include_ecommerce": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            set(payload),
            {
                "url",
                "scope",
                "overall_score",
                "pillar_scores",
                "pillar_max",
                "agent_readiness",
                "benchmark",
                "checks",
                "api_version",
            },
        )
        self.assertEqual(payload["url"], normalized_url)
        self.assertEqual(payload["overall_score"], 3)

        scope = payload["scope"]
        self.assertEqual(
            set(scope),
            {
                "include_protocols",
                "include_account_auth",
                "include_ecommerce",
                "label",
                "included_optional_surfaces",
                "excluded_optional_surfaces",
                "preset_applied",
                "overrides_applied",
                "included_families",
                "excluded_families",
                "machine_surfaces_scope",
            },
        )
        self.assertEqual(scope["include_protocols"], True)
        self.assertEqual(scope["include_account_auth"], True)
        self.assertEqual(scope["include_ecommerce"], True)
        self.assertEqual(scope["label"], "Commerce storefront + API/protocol + account/auth")
        self.assertEqual(scope["excluded_optional_surfaces"], [])

        check = next(row for row in payload["checks"] if row["check_name"] == "contract_probe")
        self.assertEqual(
            set(check),
            {
                "pillar",
                "check_name",
                "label",
                "state",
                "evidence_level",
                "available_in",
                "score",
                "max_score",
                "finding",
                "fix",
                "effort",
            },
        )
        self.assertEqual(check, _contract_check().model_dump())

        benchmark = payload["benchmark"]
        self.assertEqual(
            set(benchmark),
            {
                "score",
                "checked_score",
                "checked_max",
                "benchmark_count",
                "median_score",
                "percentile",
                "position_label",
                "nearest",
                "entries",
                "basis",
                "snapshot_date",
                "caveat",
            },
        )

        readiness = payload["agent_readiness"]
        self.assertEqual(
            set(readiness),
            {
                "score",
                "earned",
                "max",
                "label",
                "categories",
                "passed",
                "missing",
                "not_checked",
                "benchmark",
                "caveat",
            },
        )
        self.assertEqual((readiness["score"], readiness["earned"], readiness["max"]), (35, 7, 21))
        self.assertEqual(
            set(readiness["categories"][0]),
            {"name", "earned", "max", "score", "passed", "missing", "excluded"},
        )
        self.assertEqual(readiness["categories"][0]["name"], "Contract category")
        self.assertEqual(
            set(readiness["benchmark"]),
            {
                "score",
                "earned",
                "max",
                "benchmark_count",
                "median_score",
                "percentile",
                "position_label",
                "nearest",
                "entries",
                "basis",
                "snapshot_date",
                "caveat",
            },
        )
        self.assertEqual(readiness["benchmark"]["position_label"], "Contract fixture position")


if __name__ == "__main__":
    unittest.main()
