import asyncio
import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import (
    _build_context_or_error,
    _essential_group,
    _safe_agent_readiness_summary,
    _safe_check,
)
from app.models import CheckResult
from backend.tests.fixtures import make_audit_context


def _passing_check(check_name: str = "html_structure") -> CheckResult:
    return CheckResult(
        pillar="scrapability",
        check_name=check_name,
        label="Passing check",
        state="pass",
        score=1,
        max_score=1,
        finding="Check completed.",
        fix="No action needed.",
        effort="low",
    )


class AuditErrorHandlingTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_check_turns_partial_failure_into_warning_row(self) -> None:
        async def failing_check() -> CheckResult:
            raise RuntimeError("boom")

        async def passing_check() -> CheckResult:
            return _passing_check()

        failed_group = _essential_group("robots_txt")
        test_logger = logging.getLogger("app.main")
        with self.assertLogs("app.main", level="ERROR"):
            results = await asyncio.gather(
                _safe_check(failed_group, failing_check(), logger=test_logger),
                _safe_check(_essential_group("html_structure"), passing_check(), logger=test_logger),
            )

        fallback, passed = results
        self.assertEqual(fallback.check_name, "robots_txt")
        self.assertEqual(fallback.label, "Check unavailable")
        self.assertEqual(fallback.state, "warn")
        self.assertEqual(fallback.evidence_level, "unknown")
        self.assertEqual(fallback.score, 0)
        self.assertEqual(fallback.max_score, failed_group.max_score)
        self.assertIn("Retry the audit", fallback.fix)
        self.assertEqual(passed.state, "pass")

    async def test_agent_readiness_failure_returns_degraded_summary(self) -> None:
        context = make_audit_context()

        with patch(
            "app.essential_runner.build_agent_readiness_summary",
            new=AsyncMock(side_effect=RuntimeError("summary failed")),
        ):
            with self.assertLogs("app.main", level="ERROR"):
                summary = await _safe_agent_readiness_summary(
                    context,
                    include_protocols=True,
                    include_account_auth=True,
                    include_ecommerce=True,
                    logger=logging.getLogger("app.main"),
                )

        self.assertEqual(summary.label, "Agent-native unavailable")
        self.assertEqual(summary.score, 0)
        self.assertEqual(summary.earned, 0)
        # Full-scope strict-agent max is 8+6+3+4=21 (default scope is 8).
        self.assertEqual(summary.max, 21)
        self.assertEqual(summary.benchmark.max, 21)
        # Degraded-fallback now enumerates probe labels so agents can rely
        # on len(passed) + len(missing) == max independently of the error path.
        self.assertGreater(len(summary.missing), 0)
        self.assertEqual(len(summary.passed) + len(summary.missing), summary.max)

    async def test_context_setup_failure_raises_actionable_http_exception(self) -> None:
        with patch(
            "app.main.build_audit_context",
            new=AsyncMock(side_effect=RuntimeError("setup failed")),
        ):
            with self.assertLogs("app.main", level="ERROR"):
                with self.assertRaises(HTTPException) as raised:
                    await _build_context_or_error("https://example.com")

        self.assertEqual(raised.exception.status_code, 500)
        self.assertIn("could not start the audit", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
