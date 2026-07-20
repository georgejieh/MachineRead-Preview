"""MachineRead preset request model (QA5-03).

This module resolves the user-selected preset (and optional Custom/Power User
overrides) into a deterministic ``ResolvedScope`` that the rest of the audit
pipeline consumes. The contract is intentionally additive: existing callers that
send only the legacy ``include_protocols`` / ``include_account_auth`` /
``include_ecommerce`` booleans continue to work unchanged.

Precedence rules:

- When ``preset`` is provided, the preset wins. The legacy booleans are
  ignored, and ``overrides`` are applied on top of the preset defaults.
- When ``preset`` is ``None`` (legacy path), ``custom_overrides`` must be
  empty/absent and the audit uses the three booleans verbatim.
- ``preset=None`` plus non-empty ``custom_overrides`` is a 422 boundary
  violation. Power users must select ``preset="custom"`` to use overrides.

Validation rules applied by :func:`validate_overrides`:

- Unknown override keys are rejected.
- Universal-core rows and locked/paid rows cannot be toggled by free users.
- ``custom`` accepts any toggle; other presets reject keys that are
  ``not_applicable`` for that website category.
- ``include_protocols=True`` requires at least one protocol family enabled.
- ``include_account_auth=True`` requires at least one auth family enabled.
- ``include_ecommerce=True`` requires at least one commerce family enabled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


PresetName = Literal["blog", "corporate", "services", "ecommerce", "news", "saas", "custom"]
MachineSurfacesScope = Literal["common-contextual", "full"]

# Override keys that the Custom/Power User preset (and bounded Custom mode on
# top of standard presets) accepts. Keys outside this set are rejected at both
# the Pydantic boundary and inside ``validate_overrides``.
_VALID_OVERRIDE_KEYS: frozenset[str] = frozenset(
    {
        # Secondary scope dimensions (toggles the three legacy booleans).
        "protocols",
        "account_auth",
        "ecommerce",
        # Sub-signal toggles grouped by surface.
        "feed_discovery",
        "article_schema",
        "localbusiness_schema",
        "news_article_schema",
        "claimreview_schema",
        "product_offer_schema",
        "commerce_fields",
        "api_catalog",
        "mcp",
        "a2a",
        "agent_skills",
        "webmcp",
        "oauth_oidc",
        "ard_catalog",
        "auth_md",
    }
)

# The 10 always-scored Essentials rows plus ``social`` and ``wikipedia`` form
# the universal core. None of them appear in ``_VALID_OVERRIDE_KEYS``, so the
# unknown-key check already rejects attempts to toggle them. The constant is
# retained so that future surface-level universal keys fail loudly with a
# specific message instead of a generic unknown-key error.
_UNIVERSAL_CORE_KEYS: frozenset[str] = frozenset()

# Locked/paid rows (advanced coverage). Same reasoning as the universal core:
# none of them appear in ``_VALID_OVERRIDE_KEYS`` today, so the unknown-key
# check already rejects them. Kept for defense in depth and clear errors when
# new locked override keys are ever introduced.
_LOCKED_PAID_KEYS: frozenset[str] = frozenset()

# Protocol family: machine_surfaces sub-signals that imply include_protocols.
_PROTOCOL_FAMILY_KEYS: frozenset[str] = frozenset(
    {"api_catalog", "mcp", "a2a", "agent_skills", "webmcp"}
)
# Auth family: account/auth sub-signals that imply include_account_auth.
_AUTH_FAMILY_KEYS: frozenset[str] = frozenset({"oauth_oidc", "auth_md"})
# Commerce family: catalog/commerce sub-signals that imply include_ecommerce.
_ECOMMERCE_FAMILY_KEYS: frozenset[str] = frozenset(
    {"product_offer_schema", "commerce_fields"}
)

VALID_PRESETS: frozenset[str] = frozenset(
    {"blog", "corporate", "services", "ecommerce", "news", "saas", "custom"}
)


@dataclass(frozen=True)
class _PresetDefinition:
    """Internal preset catalog entry.

    ``default_families`` holds the per-preset sub-signal defaults, including
    the explicit ``False`` entries the preset considers off. ``NOT_APPLICABLE``
    keys are documented at module level for each preset below; the validator
    rejects any attempt to flip them on.
    """

    key: str
    label: str
    include_protocols: bool
    include_account_auth: bool
    include_ecommerce: bool
    machine_surfaces: MachineSurfacesScope
    default_families: dict[str, bool] = field(default_factory=dict)
    not_applicable: frozenset[str] = field(default_factory=frozenset)


# Per-preset definitions. Each preset is a deterministic identifier with the
# categories and defaults documented in docs/free_preset_taxonomy.md.
_BLOG = _PresetDefinition(
    key="blog",
    label="Blog/Content audit",
    include_protocols=False,
    include_account_auth=False,
    include_ecommerce=False,
    machine_surfaces="common-contextual",
    default_families={
        "feed_discovery": True,
        "article_schema": True,
        # speakable is tracked-only per QA4-04 and is not in _VALID_OVERRIDE_KEYS
        "localbusiness_schema": False,
        "news_article_schema": False,
        "claimreview_schema": False,
        "product_offer_schema": False,
        "commerce_fields": False,
        "api_catalog": False,
        "mcp": False,
        "a2a": False,
        "agent_skills": False,
        "webmcp": False,
        "oauth_oidc": False,
        "ard_catalog": False,
        "auth_md": False,
    },
    not_applicable=frozenset(
        {
            "localbusiness_schema",
            "news_article_schema",
            "claimreview_schema",
            "product_offer_schema",
            "commerce_fields",
            "api_catalog",
            "mcp",
            "a2a",
            "agent_skills",
            "webmcp",
            "oauth_oidc",
            "ard_catalog",
            "auth_md",
        }
    ),
)

_CORPORATE = _PresetDefinition(
    key="corporate",
    label="Corporate/Brand audit",
    include_protocols=False,
    include_account_auth=False,
    include_ecommerce=False,
    machine_surfaces="common-contextual",
    default_families={
        "feed_discovery": False,
        "article_schema": False,
        "localbusiness_schema": False,
        "news_article_schema": False,
        "claimreview_schema": False,
        "product_offer_schema": False,
        "commerce_fields": False,
        "api_catalog": False,
        "mcp": False,
        "a2a": False,
        "agent_skills": False,
        "webmcp": False,
        "oauth_oidc": False,
        "ard_catalog": False,
        "auth_md": False,
    },
    not_applicable=frozenset(
        {
            "article_schema",
            "localbusiness_schema",
            "news_article_schema",
            "claimreview_schema",
            "product_offer_schema",
            "commerce_fields",
            "api_catalog",
            "mcp",
            "a2a",
            "agent_skills",
            "webmcp",
            "oauth_oidc",
            "ard_catalog",
            "auth_md",
        }
    ),
)

_SERVICES = _PresetDefinition(
    key="services",
    label="Services/Local audit",
    include_protocols=False,
    include_account_auth=False,
    include_ecommerce=False,
    machine_surfaces="common-contextual",
    default_families={
        "feed_discovery": False,
        "article_schema": False,
        "localbusiness_schema": True,
        "news_article_schema": False,
        "claimreview_schema": False,
        "product_offer_schema": False,
        "commerce_fields": False,
        "api_catalog": False,
        "mcp": False,
        "a2a": False,
        "agent_skills": False,
        "webmcp": False,
        "oauth_oidc": False,
        "ard_catalog": False,
        "auth_md": False,
    },
    not_applicable=frozenset(
        {
            "article_schema",
            "news_article_schema",
            "claimreview_schema",
            "product_offer_schema",
            "commerce_fields",
            "api_catalog",
            "mcp",
            "a2a",
            "agent_skills",
            "webmcp",
            "oauth_oidc",
            "ard_catalog",
            "auth_md",
        }
    ),
)

_ECOMMERCE = _PresetDefinition(
    key="ecommerce",
    label="Ecommerce/Catalog audit",
    include_protocols=True,
    include_account_auth=True,
    include_ecommerce=True,
    machine_surfaces="full",
    default_families={
        "feed_discovery": True,
        "article_schema": False,
        "localbusiness_schema": False,
        "news_article_schema": False,
        "claimreview_schema": False,
        "product_offer_schema": True,
        "commerce_fields": True,
        "api_catalog": True,
        "mcp": True,
        "a2a": True,
        "agent_skills": True,
        "webmcp": True,
        "oauth_oidc": True,
        "ard_catalog": True,
        "auth_md": True,
    },
    not_applicable=frozenset(
        {
            "article_schema",
            "localbusiness_schema",
            "news_article_schema",
            "claimreview_schema",
        }
    ),
)

_NEWS = _PresetDefinition(
    key="news",
    label="News/Publisher audit",
    include_protocols=False,
    include_account_auth=False,
    include_ecommerce=False,
    machine_surfaces="common-contextual",
    default_families={
        "feed_discovery": True,
        "article_schema": True,
        "localbusiness_schema": False,
        "news_article_schema": True,
        "claimreview_schema": True,
        "product_offer_schema": False,
        "commerce_fields": False,
        "api_catalog": False,
        "mcp": False,
        "a2a": False,
        "agent_skills": False,
        "webmcp": False,
        "oauth_oidc": False,
        "ard_catalog": False,
        "auth_md": False,
    },
    not_applicable=frozenset(
        {
            "localbusiness_schema",
            "product_offer_schema",
            "commerce_fields",
            "api_catalog",
            "mcp",
            "a2a",
            "agent_skills",
            "webmcp",
            "oauth_oidc",
            "ard_catalog",
            "auth_md",
        }
    ),
)

_SAAS = _PresetDefinition(
    key="saas",
    label="SaaS/Product/API audit",
    include_protocols=True,
    include_account_auth=True,
    include_ecommerce=False,
    machine_surfaces="full",
    default_families={
        "feed_discovery": False,
        "article_schema": False,
        "localbusiness_schema": False,
        "news_article_schema": False,
        "claimreview_schema": False,
        "product_offer_schema": False,
        "commerce_fields": False,
        "api_catalog": True,
        "mcp": True,
        "a2a": True,
        "agent_skills": True,
        "webmcp": True,
        "oauth_oidc": True,
        "ard_catalog": True,
        "auth_md": True,
    },
    not_applicable=frozenset(
        {
            "article_schema",
            "localbusiness_schema",
            "news_article_schema",
            "claimreview_schema",
            "product_offer_schema",
            "commerce_fields",
        }
    ),
)

_CUSTOM = _PresetDefinition(
    key="custom",
    label="Custom/Power User audit",
    include_protocols=False,
    include_account_auth=False,
    include_ecommerce=False,
    machine_surfaces="common-contextual",
    # Custom starts from the Blog/Content base per the QA5-03 taxonomy.
    default_families=dict(_BLOG.default_families),
    not_applicable=frozenset(),  # Custom accepts every supported family.
)


PRESETS: dict[str, _PresetDefinition] = {
    "blog": _BLOG,
    "corporate": _CORPORATE,
    "services": _SERVICES,
    "ecommerce": _ECOMMERCE,
    "news": _NEWS,
    "saas": _SAAS,
    "custom": _CUSTOM,
}


@dataclass(frozen=True)
class ResolvedScope:
    """Deterministic, immutable resolved audit scope."""

    preset: str | None
    overrides: dict[str, bool]
    include_protocols: bool
    include_account_auth: bool
    include_ecommerce: bool
    included_families: tuple[str, ...]
    excluded_families: tuple[str, ...]
    machine_surfaces: MachineSurfacesScope
    preset_label: str


def _safe_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    # Coerce common truthy/falsy strings defensively without surprising callers.
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return default


def validate_overrides(preset: str, overrides: dict[str, bool] | None) -> list[str]:
    """Return a list of validation errors for the given preset + overrides.

    The list is empty when the combination is acceptable. Each entry is a
    human-readable explanation suitable for inclusion in a 422 response.
    """

    errors: list[str] = []
    if not overrides:
        return errors

    if preset not in PRESETS:
        # The Pydantic-level validator already rejects unknown preset strings,
        # but this defensive check keeps the function safe if it is ever called
        # directly from a different boundary.
        errors.append(f"Unknown preset: {preset!r}")
        return errors

    definition = PRESETS[preset]
    coerced: dict[str, bool] = {}
    for raw_key, raw_value in overrides.items():
        if not isinstance(raw_key, str):
            errors.append(f"Override key must be a string, got {type(raw_key).__name__}")
            continue
        key = raw_key
        if key not in _VALID_OVERRIDE_KEYS:
            errors.append(
                f"Unknown override key {key!r}: supported keys are "
                f"{sorted(_VALID_OVERRIDE_KEYS)}"
            )
            continue
        if key in _UNIVERSAL_CORE_KEYS:
            errors.append(
                f"Universal core family {key!r} cannot be toggled by free presets"
            )
            continue
        if key in _LOCKED_PAID_KEYS:
            errors.append(
                f"Locked/paid family {key!r} cannot be enabled by free presets"
            )
            continue
        if key in definition.not_applicable and not preset == "custom":
            errors.append(
                f"Override {key!r} is not applicable for preset {preset!r}"
            )
            continue
        coerced[key] = _safe_bool(raw_value, False)

    # Coherence checks: a true top-level dimension must have at least one
    # matching sub-signal enabled after overrides apply.
    if coerced.get("protocols") and not any(
        coerced.get(key, definition.default_families.get(key, False))
        for key in _PROTOCOL_FAMILY_KEYS
    ):
        errors.append(
            "include_protocols=true requires at least one protocol family "
            "(api_catalog, mcp, a2a, agent_skills, webmcp) to be enabled"
        )

    if coerced.get("account_auth") and not any(
        coerced.get(key, definition.default_families.get(key, False))
        for key in _AUTH_FAMILY_KEYS
    ):
        errors.append(
            "include_account_auth=true requires at least one auth family "
            "(oauth_oidc, auth_md) to be enabled"
        )

    if coerced.get("ecommerce") and not any(
        coerced.get(key, definition.default_families.get(key, False))
        for key in _ECOMMERCE_FAMILY_KEYS
    ):
        errors.append(
            "include_ecommerce=true requires at least one commerce family "
            "(product_offer_schema, commerce_fields) to be enabled"
        )

    return errors


def _resolved_machine_surfaces(
    include_protocols: bool,
    include_account_auth: bool,
    include_ecommerce: bool,
    base: MachineSurfacesScope,
) -> MachineSurfacesScope:
    """Pick the effective machine_surfaces scope.

    The base scope is the preset default. Any enabled secondary dimension
    promotes the scope to ``full`` because the strict agent-readiness surface
    list expands with protocol/auth/commerce probes.
    """

    if include_protocols or include_account_auth or include_ecommerce:
        return "full"
    return base


def _resolve_legacy(
    include_protocols: bool,
    include_account_auth: bool,
    include_ecommerce: bool,
) -> ResolvedScope:
    """Resolve a legacy boolean-only request (preset is None)."""

    machine_surfaces = _resolved_machine_surfaces(
        include_protocols, include_account_auth, include_ecommerce, "common-contextual"
    )

    included: list[str] = []
    if include_protocols:
        included.extend(sorted(_PROTOCOL_FAMILY_KEYS))
    if include_account_auth:
        included.extend(sorted(_AUTH_FAMILY_KEYS))
    if include_ecommerce:
        included.extend(sorted(_ECOMMERCE_FAMILY_KEYS))

    label_parts: list[str] = []
    if include_ecommerce:
        label_parts.append("Commerce storefront")
    else:
        label_parts.append("General website")
    if include_protocols:
        label_parts.append("API/protocol")
    if include_account_auth:
        label_parts.append("account/auth")

    label = label_parts[0] if len(label_parts) == 1 else " + ".join(label_parts)
    return ResolvedScope(
        preset=None,
        overrides={},
        include_protocols=include_protocols,
        include_account_auth=include_account_auth,
        include_ecommerce=include_ecommerce,
        included_families=tuple(included),
        excluded_families=tuple(
            sorted(
                (
                    set(_VALID_OVERRIDE_KEYS)
                    - {"protocols", "account_auth", "ecommerce"}
                    - set(included)
                )
            )
        ),
        machine_surfaces=machine_surfaces,
        preset_label=label,
    )


def _derive_implied_dimensions(
    families: dict[str, bool],
) -> tuple[bool, bool, bool]:
    """Compute the implied secondary dimensions from the resolved families.

    Enabling any sub-signal in a family turns on its top-level dimension unless
    the caller explicitly disabled it via an override. The caller is expected
    to pass the fully-resolved families dict plus the explicitly-set
    top-level dimensions so the explicit ``False`` always wins.
    """

    implied_protocols = any(families.get(key, False) for key in _PROTOCOL_FAMILY_KEYS)
    implied_auth = any(families.get(key, False) for key in _AUTH_FAMILY_KEYS)
    implied_commerce = any(
        families.get(key, False) for key in _ECOMMERCE_FAMILY_KEYS
    )
    return implied_protocols, implied_auth, implied_commerce


def resolve_scope(
    preset: str | None,
    overrides: dict[str, bool] | None,
    include_protocols: bool = False,
    include_account_auth: bool = False,
    include_ecommerce: bool = False,
) -> ResolvedScope:
    """Resolve an audit request into an immutable :class:`ResolvedScope`.

    Raises ``ValueError`` (with the joined validation messages) when the
    combination is invalid. The handler should translate that into an HTTP 422.
    """

    coerced_overrides = dict(overrides) if overrides else {}

    if preset is None:
        if coerced_overrides:
            raise ValueError(
                "custom_overrides requires an explicit preset; "
                "select preset='custom' to enable Power User overrides"
            )
        return _resolve_legacy(
            include_protocols, include_account_auth, include_ecommerce
        )

    errors = validate_overrides(preset, coerced_overrides)
    if errors:
        raise ValueError("; ".join(errors))

    definition = PRESETS[preset]

    resolved_protocols = definition.include_protocols
    resolved_auth = definition.include_account_auth
    resolved_ecom = definition.include_ecommerce

    if "protocols" in coerced_overrides:
        resolved_protocols = bool(coerced_overrides["protocols"])
    if "account_auth" in coerced_overrides:
        resolved_auth = bool(coerced_overrides["account_auth"])
    if "ecommerce" in coerced_overrides:
        resolved_ecom = bool(coerced_overrides["ecommerce"])

    resolved_families: dict[str, bool] = dict(definition.default_families)
    for key, value in coerced_overrides.items():
        if key in {"protocols", "account_auth", "ecommerce"}:
            continue
        resolved_families[key] = bool(value)

    # Implicit promotion: enabling a sub-signal in a family flips its top-level
    # dimension on unless the caller explicitly set that dimension to False.
    # The validation step above already guarantees we never end up with
    # ``dim=False`` while at least one matching family is True, so this only
    # promotes dim False -> True when a new family was just turned on.
    explicit_dims = {
        key for key in ("protocols", "account_auth", "ecommerce") if key in coerced_overrides
    }
    implied_protocols, implied_auth, implied_commerce = _derive_implied_dimensions(
        resolved_families
    )
    if "protocols" not in explicit_dims and implied_protocols:
        resolved_protocols = True
    if "account_auth" not in explicit_dims and implied_auth:
        resolved_auth = True
    if "ecommerce" not in explicit_dims and implied_commerce:
        resolved_ecom = True

    machine_surfaces = _resolved_machine_surfaces(
        resolved_protocols,
        resolved_auth,
        resolved_ecom,
        definition.machine_surfaces,
    )

    included_families = tuple(
        sorted(key for key, enabled in resolved_families.items() if enabled)
    )
    excluded_families = tuple(
        sorted(key for key, enabled in resolved_families.items() if not enabled)
    )

    return ResolvedScope(
        preset=preset,
        overrides=coerced_overrides,
        include_protocols=resolved_protocols,
        include_account_auth=resolved_auth,
        include_ecommerce=resolved_ecom,
        included_families=included_families,
        excluded_families=excluded_families,
        machine_surfaces=machine_surfaces,
        preset_label=definition.label,
    )


def benchmark_scope_key_for_scope(scope: ResolvedScope) -> str:
    """Return the p{a}_a{c} benchmark scope key for a resolved scope.

    Centralised so the contract check script and the runtime path agree on
    the format used by ``backend/app/benchmarks.py``.
    """

    return (
        f"p{int(scope.include_protocols)}_"
        f"a{int(scope.include_account_auth)}_"
        f"c{int(scope.include_ecommerce)}"
    )


__all__ = [
    "MachineSurfacesScope",
    "PRESETS",
    "PresetName",
    "ResolvedScope",
    "VALID_PRESETS",
    "benchmark_scope_key_for_scope",
    "resolve_scope",
    "validate_overrides",
]