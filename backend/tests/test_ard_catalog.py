"""Focused tests for ARD static catalog discovery and quality validation."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.checks.ard_catalog import ard_catalog_quality


class ArdCatalogQualityTests(unittest.TestCase):
    def test_valid_catalog_minimal(self) -> None:
        catalog = json.dumps({
            "specVersion": "0.9.0",
            "host": {"domain": "example.com"},
            "entries": [{
                "identifier": "urn:air:example.com:api:v1",
                "displayName": "Test API",
                "mediaType": "application/json",
                "url": "https://example.com/api",
                "description": "A test API.",
                "capabilities": ["search"],
                "tags": ["api"],
                "representativeQueries": ["test"],
                "updated": "2026-01-01T00:00:00Z",
            }],
        })
        result = ard_catalog_quality(catalog, "https://example.com")
        self.assertTrue(result["valid"])
        self.assertEqual(result["spec_version"], "0.9.0")
        self.assertEqual(result["entry_count"], 1)
        self.assertFalse(result["has_trust_manifest"])
        self.assertEqual(result["issues"], [])

    def test_valid_catalog_with_trust_manifest(self) -> None:
        catalog = json.dumps({
            "specVersion": "0.9.0",
            "host": {"domain": "example.com"},
            "entries": [],
            "trustManifest": {"algorithm": "ed25519", "publicKey": "deadbeef"},
        })
        result = ard_catalog_quality(catalog, "https://example.com")
        self.assertTrue(result["has_trust_manifest"])

    def test_empty_string_rejected(self) -> None:
        result = ard_catalog_quality("", "https://example.com")
        self.assertFalse(result["valid"])
        self.assertIn("empty", result["issues"][0].lower())

    def test_malformed_json_rejected(self) -> None:
        result = ard_catalog_quality("{not valid", "https://example.com")
        self.assertFalse(result["valid"])
        self.assertIn("not valid json", result["issues"][0].lower())

    def test_non_object_root_rejected(self) -> None:
        result = ard_catalog_quality("[1,2,3]", "https://example.com")
        self.assertFalse(result["valid"])
        self.assertIn("object", result["issues"][0].lower())

    def test_missing_spec_version(self) -> None:
        result = ard_catalog_quality(json.dumps({"host": {"domain": "example.com"}, "entries": []}), "https://example.com")
        self.assertFalse(result["valid"])
        self.assertIn("missing", result["issues"][0].lower())
        self.assertIn("specversion", result["issues"][0].lower())

    def test_non_semver_spec_version(self) -> None:
        result = ard_catalog_quality(json.dumps({
            "specVersion": "latest",
            "host": {"domain": "example.com"},
            "entries": [],
        }), "https://example.com")
        self.assertFalse(result["valid"])
        self.assertTrue(any("semver" in i.lower() for i in result["issues"]))

    def test_missing_host_block(self) -> None:
        result = ard_catalog_quality(json.dumps({
            "specVersion": "0.9.0",
            "entries": [],
        }), "https://example.com")
        self.assertFalse(result["valid"])
        self.assertTrue(any("host" in i.lower() for i in result["issues"]))

    def test_host_domain_mismatch(self) -> None:
        result = ard_catalog_quality(json.dumps({
            "specVersion": "0.9.0",
            "host": {"domain": "other-site.com"},
            "entries": [],
        }), "https://example.com")
        self.assertFalse(result["valid"])
        self.assertTrue(any("does not match" in i for i in result["issues"]))

    def test_missing_entries_list(self) -> None:
        result = ard_catalog_quality(json.dumps({
            "specVersion": "0.9.0",
            "host": {"domain": "example.com"},
        }), "https://example.com")
        self.assertFalse(result["valid"])
        self.assertTrue(any("entries" in i.lower() for i in result["issues"]))

    def test_oversized_payload_rejected(self) -> None:
        oversized = "x" * 600_000
        result = ard_catalog_quality(oversized, "https://example.com")
        self.assertFalse(result["valid"])
        self.assertIn("600000", result["issues"][0])

    def test_url_and_data_exclusivity(self) -> None:
        catalog = json.dumps({
            "specVersion": "0.9.0",
            "host": {"domain": "example.com"},
            "entries": [{
                "identifier": "urn:air:example.com:api:v1",
                "displayName": "Bad Entry",
                "mediaType": "application/json",
                "url": "https://example.com/api",
                "data": {"inline": True},
                "description": "Has both url and data.",
                "capabilities": ["search"],
                "representativeQueries": ["test"],
                "updated": "2026-01-01T00:00:00Z",
            }],
        })
        result = ard_catalog_quality(catalog, "https://example.com")
        self.assertFalse(result["valid"])
        self.assertTrue(any("exactly one" in i for i in result["issues"]))

    def test_missing_display_name(self) -> None:
        catalog = json.dumps({
            "specVersion": "0.9.0",
            "host": {"domain": "example.com"},
            "entries": [{
                "identifier": "urn:air:example.com:api:v1",
                "mediaType": "application/json",
                "url": "https://example.com/api",
                "description": "No display name.",
                "capabilities": ["search"],
                "representativeQueries": ["test"],
                "updated": "2026-01-01T00:00:00Z",
            }],
        })
        result = ard_catalog_quality(catalog, "https://example.com")
        self.assertFalse(result["valid"])
        self.assertTrue(any("displayname" in i.lower() for i in result["issues"]))

    def test_non_domain_anchored_urn(self) -> None:
        catalog = json.dumps({
            "specVersion": "0.9.0",
            "host": {"domain": "example.com"},
            "entries": [{
                "identifier": "urn:air:other.com:api:v1",
                "displayName": "Wrong Domain",
                "mediaType": "application/json",
                "url": "https://example.com/api",
                "description": "Identifier doesn't match host.",
                "capabilities": ["search"],
                "representativeQueries": ["test"],
                "updated": "2026-01-01T00:00:00Z",
            }],
        })
        result = ard_catalog_quality(catalog, "https://example.com")
        self.assertFalse(result["valid"])
        self.assertTrue(any("domain-anchored" in i.lower() for i in result["issues"]))

    def test_none_audit_url_accepted(self) -> None:
        catalog = json.dumps({
            "specVersion": "0.9.0",
            "host": {"domain": "example.com"},
            "entries": [{
                "identifier": "urn:air:example.com:api:v1",
                "displayName": "API",
                "mediaType": "application/json",
                "url": "https://example.com/api",
                "description": "Test.",
                "capabilities": ["search"],
                "representativeQueries": ["test"],
                "updated": "2026-01-01T00:00:00Z",
            }],
        })
        result = ard_catalog_quality(catalog, None)
        self.assertTrue(result["valid"])

    def test_entry_missing_updated_timestamp(self) -> None:
        catalog = json.dumps({
            "specVersion": "0.9.0",
            "host": {"domain": "example.com"},
            "entries": [{
                "identifier": "urn:air:example.com:api:v1",
                "displayName": "No Timestamp",
                "mediaType": "application/json",
                "url": "https://example.com/api",
                "description": "Missing updated.",
                "capabilities": ["search"],
                "representativeQueries": ["test"],
            }],
        })
        result = ard_catalog_quality(catalog, "https://example.com")
        self.assertFalse(result["valid"])
        self.assertTrue(any("updated" in i.lower() for i in result["issues"]))

    def test_entry_without_url_or_data(self) -> None:
        catalog = json.dumps({
            "specVersion": "0.9.0",
            "host": {"domain": "example.com"},
            "entries": [{
                "identifier": "urn:air:example.com:api:v1",
                "displayName": "No Ref",
                "mediaType": "application/json",
                "description": "Neither url nor data.",
                "capabilities": ["search"],
                "representativeQueries": ["test"],
                "updated": "2026-01-01T00:00:00Z",
            }],
        })
        result = ard_catalog_quality(catalog, "https://example.com")
        self.assertFalse(result["valid"])
        self.assertTrue(any("exactly one" in i for i in result["issues"]))

    def test_entry_data_inline_accepted(self) -> None:
        catalog = json.dumps({
            "specVersion": "0.9.0",
            "host": {"domain": "example.com"},
            "entries": [{
                "identifier": "urn:air:example.com:api:v1",
                "displayName": "Inline Entry",
                "mediaType": "application/json",
                "data": {"key": "value"},
                "description": "Uses data instead of url.",
                "capabilities": ["search"],
                "representativeQueries": ["test"],
                "updated": "2026-01-01T00:00:00Z",
            }],
        })
        result = ard_catalog_quality(catalog, "https://example.com")
        self.assertTrue(result["valid"])

    def test_issues_list_includes_all_problems(self) -> None:
        catalog = json.dumps({
            "specVersion": "bad-ver",
            "host": {},
            "entries": [{
                "displayName": "Broken Entry",
                "description": "Missing many fields.",
            }],
        })
        result = ard_catalog_quality(catalog, "https://example.com")
        self.assertFalse(result["valid"])
        self.assertGreater(len(result["issues"]), 0)


if __name__ == "__main__":
    unittest.main()