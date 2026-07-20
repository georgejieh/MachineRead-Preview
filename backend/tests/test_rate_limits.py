import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import DEFAULT_AUDIT_RATE_LIMIT, audit_rate_limit


class RateLimitConfigTests(unittest.TestCase):
    def test_audit_rate_limit_uses_local_default_when_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(audit_rate_limit(), DEFAULT_AUDIT_RATE_LIMIT)

    def test_audit_rate_limit_uses_configured_value(self) -> None:
        with patch.dict(os.environ, {"MACHINEREAD_AUDIT_RATE_LIMIT": "10/minute"}):
            self.assertEqual(audit_rate_limit(), "10/minute")

    def test_audit_rate_limit_strips_and_falls_back_when_blank(self) -> None:
        with patch.dict(os.environ, {"MACHINEREAD_AUDIT_RATE_LIMIT": "   "}):
            self.assertEqual(audit_rate_limit(), DEFAULT_AUDIT_RATE_LIMIT)

        with patch.dict(os.environ, {"MACHINEREAD_AUDIT_RATE_LIMIT": " 5/minute "}):
            self.assertEqual(audit_rate_limit(), "5/minute")


if __name__ == "__main__":
    unittest.main()
