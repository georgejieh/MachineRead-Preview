"""Preset model unit tests.

Covers the 7-preset catalog, ResolvedScope resolution, validation rules,
precedence over legacy booleans, backward compatibility, and the benchmark
contract invariants that the preset model must preserve.
"""

from __future__ import annotations

import sys
import unittest
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import benchmarks
from app.presets import (
    PRESETS,
    VALID_PRESETS,
    ResolvedScope,
    benchmark_scope_key_for_scope,
    resolve_scope,
    validate_overrides,
)


class PresetCatalogTests(unittest.TestCase):
    """Catalog-level invariants that the rest of the tests rely on."""

    def test_all_seven_presets_are_exposed(self) -> None:
        self.assertEqual(
            set(PRESETS),
            {"blog", "corporate", "services", "ecommerce", "news", "saas", "custom"},
        )
        self.assertEqual(VALID_PRESETS, set(PRESETS))

    def test_blog_is_common_contextual_with_p0_a0_c0(self) -> None:
        scope = resolve_scope("blog", None)
        self.assertEqual(scope.preset, "blog")
        self.assertFalse(scope.include_protocols)
        self.assertFalse(scope.include_account_auth)
        self.assertFalse(scope.include_ecommerce)
        self.assertEqual(scope.machine_surfaces, "common-contextual")
        self.assertEqual(benchmark_scope_key_for_scope(scope), "p0_a0_c0")

    def test_corporate_is_common_contextual_with_p0_a0_c0(self) -> None:
        scope = resolve_scope("corporate", None)
        self.assertFalse(scope.include_protocols)
        self.assertFalse(scope.include_account_auth)
        self.assertFalse(scope.include_ecommerce)
        self.assertEqual(scope.machine_surfaces, "common-contextual")
        self.assertEqual(benchmark_scope_key_for_scope(scope), "p0_a0_c0")

    def test_services_is_common_contextual_with_p0_a0_c0(self) -> None:
        scope = resolve_scope("services", None)
        self.assertFalse(scope.include_protocols)
        self.assertFalse(scope.include_account_auth)
        self.assertFalse(scope.include_ecommerce)
        self.assertEqual(scope.machine_surfaces, "common-contextual")
        self.assertEqual(benchmark_scope_key_for_scope(scope), "p0_a0_c0")

    def test_ecommerce_is_full_protocol_scope_with_p1_a1_c1(self) -> None:
        scope = resolve_scope("ecommerce", None)
        self.assertTrue(scope.include_protocols)
        self.assertTrue(scope.include_account_auth)
        self.assertTrue(scope.include_ecommerce)
        self.assertEqual(scope.machine_surfaces, "full")
        self.assertEqual(benchmark_scope_key_for_scope(scope), "p1_a1_c1")

    def test_news_is_common_contextual_with_p0_a0_c0(self) -> None:
        scope = resolve_scope("news", None)
        self.assertFalse(scope.include_protocols)
        self.assertFalse(scope.include_account_auth)
        self.assertFalse(scope.include_ecommerce)
        self.assertEqual(scope.machine_surfaces, "common-contextual")
        self.assertEqual(benchmark_scope_key_for_scope(scope), "p0_a0_c0")

    def test_saas_is_full_protocol_scope_with_p1_a1_c0(self) -> None:
        scope = resolve_scope("saas", None)
        self.assertTrue(scope.include_protocols)
        self.assertTrue(scope.include_account_auth)
        self.assertFalse(scope.include_ecommerce)
        self.assertEqual(scope.machine_surfaces, "full")
        self.assertEqual(benchmark_scope_key_for_scope(scope), "p1_a1_c0")

    def test_custom_defaults_to_blog_base(self) -> None:
        scope = resolve_scope("custom", None)
        # Custom starts from the Blog/Content base.
        blog_scope = resolve_scope("blog", None)
        self.assertEqual(scope.include_protocols, blog_scope.include_protocols)
        self.assertEqual(
            scope.include_account_auth, blog_scope.include_account_auth
        )
        self.assertEqual(scope.include_ecommerce, blog_scope.include_ecommerce)
        self.assertEqual(scope.machine_surfaces, "common-contextual")
        self.assertEqual(
            scope.included_families, blog_scope.included_families
        )
        self.assertEqual(scope.preset, "custom")


class CustomOverrideTests(unittest.TestCase):
    """Custom/Power User override resolution and validation."""

    def test_custom_with_no_overrides_matches_blog(self) -> None:
        custom = resolve_scope("custom", None)
        blog = resolve_scope("blog", None)
        self.assertEqual(custom.include_protocols, blog.include_protocols)
        self.assertEqual(
            custom.include_account_auth, blog.include_account_auth
        )
        self.assertEqual(custom.include_ecommerce, blog.include_ecommerce)
        self.assertEqual(custom.included_families, blog.included_families)

    def test_custom_can_toggle_a_family_on(self) -> None:
        scope = resolve_scope(
            "custom", {"oauth_oidc": True, "auth_md": True}
        )
        self.assertTrue("oauth_oidc" in scope.included_families)
        self.assertTrue("auth_md" in scope.included_families)
        self.assertTrue(scope.include_account_auth)

    def test_custom_can_disable_a_default_family(self) -> None:
        scope = resolve_scope("custom", {"feed_discovery": False})
        self.assertNotIn("feed_discovery", scope.included_families)
        self.assertIn("feed_discovery", scope.excluded_families)

    def test_custom_can_toggle_protocol_dimensions(self) -> None:
        scope = resolve_scope(
            "custom",
            {"protocols": True, "api_catalog": True, "mcp": True},
        )
        self.assertTrue(scope.include_protocols)
        self.assertEqual(scope.machine_surfaces, "full")
        self.assertIn("api_catalog", scope.included_families)
        self.assertIn("mcp", scope.included_families)

    def test_validate_overrides_unknown_key_returns_error(self) -> None:
        errors = validate_overrides("custom", {"definitely_unknown": True})
        self.assertTrue(any("Unknown override key" in e for e in errors))

    def test_validate_overrides_universal_core_key_returns_error(self) -> None:
        # ``robots_txt`` is not in the override key set, so it falls through to
        # the unknown-key branch. This proves the universal core is rejected.
        errors = validate_overrides("custom", {"robots_txt": True})
        self.assertTrue(errors)
        self.assertTrue(any("Unknown override key" in e for e in errors))

    def test_validate_overrides_locked_key_returns_error(self) -> None:
        errors = validate_overrides("custom", {"earned_mentions_backlinks": True})
        self.assertTrue(errors)

    def test_validate_overrides_rejects_not_applicable_family(self) -> None:
        # LocalBusiness is not applicable for the Blog preset.
        errors = validate_overrides("blog", {"localbusiness_schema": True})
        self.assertTrue(any("not applicable" in e for e in errors))

    def test_validate_overrides_rejects_protocols_with_no_protocol_family(self) -> None:
        errors = validate_overrides("custom", {"protocols": True})
        self.assertTrue(
            any("protocol family" in e for e in errors),
            f"expected protocol family error, got {errors}",
        )

    def test_validate_overrides_rejects_account_auth_with_no_auth_family(self) -> None:
        errors = validate_overrides("custom", {"account_auth": True})
        self.assertTrue(
            any("auth family" in e for e in errors),
            f"expected auth family error, got {errors}",
        )

    def test_validate_overrides_rejects_ecommerce_with_no_commerce_family(self) -> None:
        errors = validate_overrides("custom", {"ecommerce": True})
        self.assertTrue(
            any("commerce family" in e for e in errors),
            f"expected commerce family error, got {errors}",
        )

    def test_validate_overrides_empty_dict_passes(self) -> None:
        self.assertEqual(validate_overrides("blog", {}), [])

    def test_validate_overrides_none_passes(self) -> None:
        self.assertEqual(validate_overrides("blog", None), [])


class PrecedenceTests(unittest.TestCase):
    """Preset wins over legacy booleans; legacy path still works."""

    def test_preset_wins_over_legacy_booleans(self) -> None:
        # Legacy booleans say everything on, but blog preset should still
        # resolve to p0_a0_c0.
        scope = resolve_scope(
            "blog", None, True, True, True
        )
        self.assertEqual(scope.preset, "blog")
        self.assertFalse(scope.include_protocols)
        self.assertFalse(scope.include_account_auth)
        self.assertFalse(scope.include_ecommerce)

    def test_legacy_preset_none_with_booleans_works(self) -> None:
        scope = resolve_scope(None, None, True, False, True)
        self.assertIsNone(scope.preset)
        self.assertTrue(scope.include_protocols)
        self.assertFalse(scope.include_account_auth)
        self.assertTrue(scope.include_ecommerce)
        self.assertEqual(scope.machine_surfaces, "full")
        self.assertEqual(benchmark_scope_key_for_scope(scope), "p1_a0_c1")

    def test_legacy_preset_none_with_no_overrides_works(self) -> None:
        scope = resolve_scope(None, None)
        self.assertIsNone(scope.preset)
        self.assertFalse(scope.include_protocols)
        self.assertFalse(scope.include_account_auth)
        self.assertFalse(scope.include_ecommerce)
        self.assertEqual(scope.machine_surfaces, "common-contextual")

    def test_preset_none_with_overrides_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            resolve_scope(None, {"feed_discovery": True})
        self.assertIn("custom_overrides", str(ctx.exception).lower())
        self.assertIn("preset", str(ctx.exception).lower())

    def test_overrides_override_preset_dimensions(self) -> None:
        # Ecommerce default is p1_a1_c1; explicitly turning off account_auth
        # via override should be accepted (it's a valid Custom-style toggle).
        scope = resolve_scope("ecommerce", {"account_auth": False})
        self.assertTrue(scope.include_protocols)
        self.assertFalse(scope.include_account_auth)
        self.assertTrue(scope.include_ecommerce)


class BenchmarkContractTests(unittest.TestCase):
    """The resolved scopes must map cleanly onto the 8-variant benchmark
    contract documented in scripts/check_score_contract.py."""

    def _all_resolved_keys(self) -> set[str]:
        """Yield every benchmark key produced by any preset under any
        legacy-boolean override (which Custom uses to bypass the validation
        gate for power-user combinations)."""

        keys: set[str] = set()
        for preset in PRESETS:
            for (
                include_protocols,
                include_account_auth,
                include_ecommerce,
            ) in product((False, True), repeat=3):
                # Custom accepts every combination; the standard presets
                # validate over their default scope so we only exercise the
                # default resolution path here.
                if preset == "custom":
                    keys.add(
                        benchmark_scope_key_for_scope(
                            resolve_scope(preset, None)
                        )
                    )
                else:
                    keys.add(
                        benchmark_scope_key_for_scope(
                            resolve_scope(
                                preset,
                                None,
                                include_protocols,
                                include_account_auth,
                                include_ecommerce,
                            )
                        )
                    )
        return keys

    def test_all_seven_presets_produce_a_key_in_the_eight_variant_set(self) -> None:
        expected_keys = {
            benchmarks.benchmark_scope_key(p, a, c)
            for p, a, c in product((False, True), repeat=3)
        }
        observed_keys = self._all_resolved_keys()
        self.assertTrue(
            observed_keys.issubset(expected_keys),
            f"preset keys out of contract: {observed_keys}",
        )

    def test_all_seven_presets_produce_strict_agent_max_in_expected_set(self) -> None:
        expected_maxes = {8, 11, 12, 14, 15, 17, 18, 21}
        observed_maxes: set[int] = set()
        for preset in PRESETS:
            for (
                include_protocols,
                include_account_auth,
                include_ecommerce,
            ) in product((False, True), repeat=3):
                if preset == "custom":
                    scope = resolve_scope(preset, None)
                else:
                    scope = resolve_scope(
                        preset,
                        None,
                        include_protocols,
                        include_account_auth,
                        include_ecommerce,
                    )
                observed_maxes.add(
                    benchmarks._agent_max_for_scope(
                        scope.include_protocols,
                        scope.include_account_auth,
                        scope.include_ecommerce,
                    )
                )
        self.assertTrue(
            observed_maxes.issubset(expected_maxes),
            f"strict agent max out of contract: {observed_maxes}",
        )


class ResolvedScopeShapeTests(unittest.TestCase):
    """ResolvedScope shape invariants consumed by the API response."""

    def test_resolved_scope_included_families_is_sorted(self) -> None:
        scope = resolve_scope(
            "custom",
            {"webmcp": True, "api_catalog": True, "mcp": True},
        )
        self.assertEqual(
            list(scope.included_families),
            sorted(scope.included_families),
        )

    def test_resolved_scope_excluded_families_is_sorted(self) -> None:
        scope = resolve_scope("blog", None)
        self.assertEqual(
            list(scope.excluded_families),
            sorted(scope.excluded_families),
        )

    def test_resolved_scope_is_frozen(self) -> None:
        scope = resolve_scope("blog", None)
        with self.assertRaises(Exception):
            scope.preset = "saas"  # type: ignore[misc]

    def test_resolved_scope_preset_label_uses_preset_definition_label(self) -> None:
        scope = resolve_scope("ecommerce", None)
        self.assertEqual(scope.preset_label, "Ecommerce/Catalog audit")

    def test_resolved_scope_legacy_label_uses_legacy_format(self) -> None:
        scope = resolve_scope(None, None, True, False, True)
        self.assertEqual(scope.preset_label, "Commerce storefront + API/protocol")


if __name__ == "__main__":
    unittest.main()