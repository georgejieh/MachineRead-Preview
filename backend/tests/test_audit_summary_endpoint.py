"""Endpoint tests for POST /v1/audit/summary.

The summary endpoint shares the FastAPI ``app`` instance with ``/v1/audit``
and ``/audit``, reuses the same rate-limit bucket, the same 200/400/422/429/500
error contracts, and projects a full ``AuditResult`` through
``build_report_summary``. These tests verify the wire contract end-to-end
without making network calls.
"""

from __future__ import annotations

import os
import sys
import unittest
from contextlib import ExitStack
from itertools import product
from pathlib import Path
from unittest.mock import AsyncMock, patch

from limits import parse

# Rate-limit isolation: must precede the ``TestClient`` import in any test
# collection that imports app.main. The env-var pattern is from
# ``machineread-testing``.
os.environ["MACHINEREAD_AUDIT_RATE_LIMIT"] = "1000/minute"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.main import app, limiter
from app.models import (
    AgentBenchmarkComparison,
    AgentReadinessCategory,
    AgentReadinessSummary,
    AuditResult,
    CheckResult,
)


def _stub_check() -> CheckResult:
    return CheckResult(
        pillar="scrapability",
        check_name="robots_txt",
        label="Summary endpoint probe",
        state="partial",
        evidence_level="verified",
        available_in="Essentials summary fixture",
        score=3,
        max_score=5,
        finding="Probe fixture for the summary endpoint.",
        fix="Replace with real fixture data.",
        effort="medium",
    )


def _agent_readiness(*, include_protocols: bool, include_account_auth: bool, include_ecommerce: bool) -> AgentReadinessSummary:
    max_value = (
        8
        + (6 if include_protocols else 0)
        + (3 if include_account_auth else 0)
        + (4 if include_ecommerce else 0)
    )
    return AgentReadinessSummary(
        score=round((7 / max_value) * 100) if max_value else 0,
        earned=7,
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
            score=round((7 / max_value) * 100) if max_value else 0,
            earned=7,
            max=max_value,
            benchmark_count=0,
            median_score=0,
            percentile=100,
            position_label="Summary fixture position",
            nearest=[],
            entries=[],
            basis="Summary fixture agent benchmark basis",
            snapshot_date="2026-07-18",
            caveat="Summary fixture agent benchmark caveat.",
        ),
        caveat="Summary fixture readiness caveat.",
    )


class _SummaryEndpointBase(unittest.TestCase):
    def setUp(self) -> None:
        # Reset SlowAPI's in-memory counters so each test starts with a clean
        # shared audit bucket.
        limiter._storage.reset()
        self._client = TestClient(app)

    def tearDown(self) -> None:
        limiter._storage.reset()

    def _mocked_post(
        self,
        client: TestClient,
        path: str,
        body: dict,
        *,
        url: str = "https://summary.example/",
    ) -> dict:
        """POST ``body`` and return ``(status, json)`` after mocking the
        full pipeline. Keeps each test hermetic and fast."""
        with (
            patch("app.main.validate_url", return_value=url),
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
                new=AsyncMock(
                    side_effect=lambda ctx, p, a, c, **_: _agent_readiness(
                        include_protocols=p,
                        include_account_auth=a,
                        include_ecommerce=c,
                    )
                ),
            ),
        ):
            response = client.post(path, json=body)
        return {
            "status_code": response.status_code,
            "json": response.json() if response.content else {},
            "headers": dict(response.headers),
        }


class HappyPathTests(_SummaryEndpointBase):
    def test_post_summary_valid_url_returns_200(self) -> None:
        result = self._mocked_post(
            self._client,
            "/v1/audit/summary",
            {"url": "https://summary.example/"},
        )
        self.assertEqual(result["status_code"], 200)
        payload = result["json"]
        # Top-level keys must match the strict AuditSummary contract.
        self.assertEqual(
            set(payload),
            {
                "api_version",
                "summary_version",
                "url",
                "scope",
                "scores",
                "benchmarks",
                "checks",
                "attention",
                "limitations",
            },
        )
        self.assertEqual(payload["api_version"], "1.0")
        self.assertEqual(payload["summary_version"], "1.0")
        self.assertEqual(payload["url"], "https://summary.example/")
        self.assertEqual(
            payload["limitations"],
            [
                "relative_scores",
                "no_live_ranking",
                "no_provider_ip_auth",
                "no_paid_crawlers",
            ],
        )


class ErrorContractTests(_SummaryEndpointBase):
    def test_post_summary_ssrf_target_returns_400(self) -> None:
        # Use a real loopback URL so validate_url actually triggers.
        self._client = TestClient(app)
        response = self._client.post(
            "/v1/audit/summary", json={"url": "http://127.0.0.1/"}
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertIn("detail", body)
        self.assertIn("private address", str(body["detail"]))

    def test_post_summary_invalid_body_returns_422(self) -> None:
        response = self._client.post("/v1/audit/summary", json={})
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertIn("detail", body)
        self.assertTrue(body["detail"])

    def test_post_summary_bad_preset_returns_422(self) -> None:
        response = self._client.post(
            "/v1/audit/summary",
            json={"url": "https://summary.example/", "preset": "invalid_preset"},
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertIn("detail", body)
        joined = str(body["detail"])
        self.assertIn("preset", joined.lower())

    def test_post_summary_rate_limited_returns_429(self) -> None:
        # Temporarily lower the registered summary-route limit; changing the
        # environment after module import would not affect SlowAPI's decorator.
        route_limit = limiter._route_limits["app.main.audit_summary"][0]
        with patch.object(route_limit, "limit", parse("1/minute")):
            client = TestClient(app)
            with (
                patch("app.main.validate_url", return_value="https://summary.example/"),
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
                    new=AsyncMock(
                        side_effect=lambda ctx, p, a, c, **_: _agent_readiness(
                            include_protocols=p,
                            include_account_auth=a,
                            include_ecommerce=c,
                        )
                    ),
                ),
            ):
                first = client.post(
                    "/v1/audit/summary",
                    json={"url": "https://summary.example/"},
                )
            self.assertEqual(first.status_code, 200)
            second = client.post(
                "/v1/audit/summary",
                json={"url": "https://summary.example/"},
            )
        self.assertEqual(second.status_code, 429)
        body = second.json()
        self.assertIn("detail", body)
        self.assertIn("retry_after", body)

    def test_post_summary_server_error_returns_500(self) -> None:
        with (
            patch("app.main.validate_url", return_value="https://summary.example/"),
            patch(
                "app.main.build_audit_context",
                new=AsyncMock(side_effect=RuntimeError("setup failed")),
            ),
        ):
            response = self._client.post(
                "/v1/audit/summary",
                json={"url": "https://summary.example/"},
            )
        self.assertEqual(response.status_code, 500)
        body = response.json()
        self.assertIn("detail", body)
        self.assertIn("could not start the audit", body["detail"])


class SharedBucketTests(_SummaryEndpointBase):
    def test_alternating_calls_share_bucket(self) -> None:
        """An audit call on /v1/audit must burn budget that /v1/audit/summary
        can no longer use, and vice versa. This proves the two endpoints share
        the same logical rate-limit bucket rather than each having its own."""
        client = TestClient(app)
        audit_limit = limiter._route_limits["app.main.audit"][0]
        summary_limit = limiter._route_limits["app.main.audit_summary"][0]
        small_limit = parse("2/minute")
        with (
            patch.object(audit_limit, "limit", small_limit),
            patch.object(summary_limit, "limit", small_limit),
            ExitStack() as stack,
        ):
            stack.enter_context(
                patch("app.main.validate_url", return_value="https://summary.example/")
            )
            stack.enter_context(
                patch(
                    "app.main._build_context_or_error",
                    new=AsyncMock(return_value=object()),
                )
            )
            stack.enter_context(
                patch(
                    "app.main._run_essential_checks",
                    new=AsyncMock(return_value=[_stub_check()]),
                )
            )
            stack.enter_context(
                patch(
                    "app.main._safe_agent_readiness_summary",
                    new=AsyncMock(
                        side_effect=lambda ctx, p, a, c, **_: _agent_readiness(
                            include_protocols=p,
                            include_account_auth=a,
                            include_ecommerce=c,
                        )
                    ),
                )
            )
            first_audit = client.post(
                "/v1/audit",
                json={"url": "https://summary.example/"},
            )
            first_summary = client.post(
                "/v1/audit/summary",
                json={"url": "https://summary.example/"},
            )
            second_audit = client.post(
                "/v1/audit",
                json={"url": "https://summary.example/"},
            )
            second_summary = client.post(
                "/v1/audit/summary",
                json={"url": "https://summary.example/"},
            )
        self.assertEqual(first_audit.status_code, 200)
        self.assertEqual(first_summary.status_code, 200)
        self.assertEqual(second_audit.status_code, 429)
        self.assertEqual(second_summary.status_code, 429)


class PrivateFieldBoundaryTests(_SummaryEndpointBase):
    def test_no_finding_or_fix_prose_in_payload(self) -> None:
        result = self._mocked_post(
            self._client,
            "/v1/audit/summary",
            {"url": "https://summary.example/"},
        )
        self.assertEqual(result["status_code"], 200)
        payload = result["json"]
        forbidden_strings = ["Probe fixture for the summary endpoint.", "Replace with real fixture data."]
        for needle in forbidden_strings:
            self.assertNotIn(
                needle,
                str(payload),
                f"forbidden prose {needle!r} leaked into summary payload",
            )

    def test_no_benchmark_entries_or_peer_data_in_payload(self) -> None:
        result = self._mocked_post(
            self._client,
            "/v1/audit/summary",
            {"url": "https://summary.example/"},
        )
        self.assertEqual(result["status_code"], 200)
        payload = result["json"]
        forbidden_keys = [
            "entries",
            "nearest",
            "basis",
            "caveat",
            "passed",
            "missing",
            "not_checked",
        ]
        for key in forbidden_keys:
            self.assertNotIn(
                key,
                str(payload),
                f"forbidden benchmark/peers key {key!r} leaked into summary",
            )


class ScopeCoverageTests(_SummaryEndpointBase):
    def test_all_eight_scope_variants_return_200(self) -> None:
        for include_protocols, include_account_auth, include_ecommerce in product(
            (False, True), repeat=3
        ):
            with self.subTest(
                p=include_protocols,
                a=include_account_auth,
                c=include_ecommerce,
            ):
                result = self._mocked_post(
                    self._client,
                    "/v1/audit/summary",
                    {
                        "url": "https://summary.example/",
                        "include_protocols": include_protocols,
                        "include_account_auth": include_account_auth,
                        "include_ecommerce": include_ecommerce,
                    },
                )
                self.assertEqual(result["status_code"], 200)
                expected_max = (
                    8
                    + (6 if include_protocols else 0)
                    + (3 if include_account_auth else 0)
                    + (4 if include_ecommerce else 0)
                )
                self.assertEqual(
                    result["json"]["scores"]["agent_readiness"]["max"],
                    expected_max,
                )


class PipelineRoutingTests(_SummaryEndpointBase):
    def test_summary_endpoint_uses_execute_audit_helper(self) -> None:
        """Both ``/v1/audit`` and ``/v1/audit/summary`` must call the
        shared ``_execute_audit`` helper so the canonical pipeline runs
        once and the rate-limit bucket stays shared."""
        from app import main as backend_main

        with (
            patch("app.main.validate_url", return_value="https://summary.example/"),
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
                new=AsyncMock(
                    return_value=_agent_readiness(
                        include_protocols=False,
                        include_account_auth=False,
                        include_ecommerce=False,
                    )
                ),
            ),
            patch(
                "app.main._execute_audit",
                wraps=backend_main._execute_audit,
            ) as spy,
        ):
            response = self._client.post(
                "/v1/audit/summary",
                json={"url": "https://summary.example/"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(spy.called)


if __name__ == "__main__":
    unittest.main()
