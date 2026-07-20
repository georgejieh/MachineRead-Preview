import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import DEFAULT_LOCAL_FRONTEND_ORIGINS, cors_allowed_origins


class CorsConfigTests(unittest.TestCase):
    def test_cors_origins_use_local_defaults_in_dev(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(cors_allowed_origins(), DEFAULT_LOCAL_FRONTEND_ORIGINS)

    def test_cors_origins_add_configured_values_in_dev(self) -> None:
        configured = " https://app.example.com, ,http://localhost:3000,https://preview.example.com "
        with patch.dict(os.environ, {"MACHINEREAD_CORS_ORIGINS": configured}, clear=True):
            self.assertEqual(
                cors_allowed_origins(),
                [
                    *DEFAULT_LOCAL_FRONTEND_ORIGINS,
                    "https://app.example.com",
                    "https://preview.example.com",
                ],
            )

    def test_cors_origins_use_only_configured_values_in_production(self) -> None:
        configured = "https://app.example.com, https://app.example.com, https://admin.example.com"
        with patch.dict(
            os.environ,
            {"ENVIRONMENT": " production ", "MACHINEREAD_CORS_ORIGINS": configured},
            clear=True,
        ):
            self.assertEqual(
                cors_allowed_origins(),
                ["https://app.example.com", "https://admin.example.com"],
            )

    def test_cors_origins_fail_closed_in_production_when_unset_or_blank(self) -> None:
        with patch.dict(os.environ, {"ENVIRONMENT": "Production"}, clear=True):
            self.assertEqual(cors_allowed_origins(), [])

        with patch.dict(
            os.environ,
            {"ENVIRONMENT": "production", "MACHINEREAD_CORS_ORIGINS": " ,  , "},
            clear=True,
        ):
            self.assertEqual(cors_allowed_origins(), [])


if __name__ == "__main__":
    unittest.main()
