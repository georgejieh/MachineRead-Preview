/**
 * Frontend mirror of the preset catalog.
 *
 * This module exposes UI-only metadata for the preset picker. It is NOT a
 * substitute for backend validation in `backend/app/presets.py` — the
 * backend remains the source of truth for what is actually scored. The
 * mirror is consumed by:
 *
 * - `components/preset-picker.tsx` (cards and selection state)
 * - `components/custom-overrides-panel.tsx` (category grouping and
 *   per-preset "not applicable" disable tooltips)
 *
 * When the backend preset catalog changes, this UI mirror must be updated
 * in parallel so the picker keeps describing what the backend will do.
 */

import type { Preset } from "@/lib/types";

export interface PresetDisplayEntry {
  /** Human-readable preset label (matches `backend/app/presets.py` `label`). */
  label: string;
  /** Short one-line description, sourced from the taxonomy doc. */
  description: string;
  /** Number of optional check families included by default for this preset. */
  familyCount: number;
  /** Human-readable list of included check families (for the card tooltip / body). */
  families: string[];
  /**
   * Override keys that are not applicable for this preset. Mirrors
   * `not_applicable` in `backend/app/presets.py`. The Custom card always
   * receives an empty `notAvailable` array.
   */
  notAvailable: string[];
}

/**
 * Override key labels. Keys come straight from
 * `backend/app/presets.py::_VALID_OVERRIDE_KEYS`. The frontend never
 * invents new keys; if a key is missing here, the backend will reject it.
 */
export const OVERRIDE_LABELS: Record<string, string> = {
  protocols: "API & protocol surfaces",
  account_auth: "Account & auth surfaces",
  ecommerce: "Commerce surfaces",
  feed_discovery: "Feed discovery (RSS/Atom)",
  article_schema: "Article / BlogPosting schema",
  localbusiness_schema: "LocalBusiness schema",
  news_article_schema: "NewsArticle schema",
  claimreview_schema: "ClaimReview schema",
  product_offer_schema: "Product / Offer schema",
  commerce_fields: "Commerce fields (SKU/GTIN/price)",
  api_catalog: "API catalog (OpenAPI)",
  mcp: "Model Context Protocol (MCP)",
  a2a: "Agent-to-agent (A2A)",
  agent_skills: "Agent Skills catalog",
  webmcp: "WebMCP surface",
  oauth_oidc: "OAuth / OIDC discovery",
  ard_catalog: "AgentReady / ARD catalog",
  auth_md: "auth.md surface",
};

export type OverrideCategory = "Content" | "Protocols" | "Commerce" | "Auth";

export interface OverrideCategoryGroup {
  category: OverrideCategory;
  /** Keys in display order. */
  keys: string[];
}

export const OVERRIDE_CATEGORIES: OverrideCategoryGroup[] = [
  {
    category: "Content",
    keys: ["feed_discovery", "article_schema", "localbusiness_schema", "news_article_schema", "claimreview_schema"],
  },
  {
    category: "Protocols",
    keys: ["api_catalog", "mcp", "a2a", "agent_skills", "webmcp"],
  },
  {
    category: "Commerce",
    keys: ["product_offer_schema", "commerce_fields"],
  },
  {
    category: "Auth",
    keys: ["oauth_oidc", "auth_md"],
  },
];

/**
 * Top-level secondary dimensions live alongside the per-family overrides so
 * users can toggle the legacy booleans from one place in Custom mode.
 */
export const SECONDARY_DIMENSION_KEYS = ["protocols", "account_auth", "ecommerce"] as const;

/**
 * PRESET_DISPLAY: UI metadata keyed by every `Preset` literal from
 * `frontend/lib/types.ts`. Mirrors `_PresetDefinition` in
 * `backend/app/presets.py` plus the per-preset description text.
 *
 * `familyCount` counts only the per-preset sub-signal families that the
 * preset enables (the legacy top-level dimensions are not counted as
 * "families" for the card body so the count reflects included optional
 * surfaces).
 */
export const PRESET_DISPLAY: Record<Preset, PresetDisplayEntry> = {
  blog: {
    label: "Blog/Content audit",
    description: "Personal blogs, content sites, newsletters, and topical essays that publish dated articles.",
    familyCount: 2,
    families: ["Feed discovery (RSS/Atom)", "Article / BlogPosting schema"],
    notAvailable: [
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
    ],
  },
  corporate: {
    label: "Corporate/Brand audit",
    description: "Company sites, brand pages, portfolios, and agency homepages.",
    familyCount: 0,
    families: [],
    notAvailable: [
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
    ],
  },
  services: {
    label: "Services/Local audit",
    description: "Local businesses, service providers, contractors, professional services, and clinics.",
    familyCount: 1,
    families: ["LocalBusiness schema"],
    notAvailable: [
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
    ],
  },
  ecommerce: {
    label: "Ecommerce/Catalog audit",
    description: "Online stores, product catalogs, marketplaces, and DTC sellers with SKUs/GTINs and pricing.",
    familyCount: 11,
    families: [
      "Feed discovery (RSS/Atom)",
      "Product / Offer schema",
      "Commerce fields (SKU/GTIN/price)",
      "API catalog (OpenAPI)",
      "Model Context Protocol (MCP)",
      "Agent-to-agent (A2A)",
      "Agent Skills catalog",
      "WebMCP surface",
      "OAuth / OIDC discovery",
      "AgentReady / ARD catalog",
      "auth.md surface",
    ],
    notAvailable: [
      "article_schema",
      "localbusiness_schema",
      "news_article_schema",
      "claimreview_schema",
    ],
  },
  news: {
    label: "News/Publisher audit",
    description: "News sites, magazines, trade publications, and editorial publishers that publish dated or investigative content.",
    familyCount: 4,
    families: [
      "Feed discovery (RSS/Atom)",
      "Article / BlogPosting schema",
      "NewsArticle schema",
      "ClaimReview schema",
    ],
    notAvailable: [
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
    ],
  },
  saas: {
    label: "SaaS/Product/API audit",
    description: "SaaS platforms, API products, developer tools, and B2B software that publish OpenAPI, MCP, or webhooks.",
    familyCount: 8,
    families: [
      "API catalog (OpenAPI)",
      "Model Context Protocol (MCP)",
      "Agent-to-agent (A2A)",
      "Agent Skills catalog",
      "WebMCP surface",
      "OAuth / OIDC discovery",
      "AgentReady / ARD catalog",
      "auth.md surface",
    ],
    notAvailable: [
      "article_schema",
      "localbusiness_schema",
      "news_article_schema",
      "claimreview_schema",
      "product_offer_schema",
      "commerce_fields",
    ],
  },
  custom: {
    label: "Custom / Power User audit",
    description: "Expert mode. Defaults to Blog/Content; manually toggle any supported family on top.",
    familyCount: 2,
    families: ["Feed discovery (RSS/Atom)", "Article / BlogPosting schema"],
    notAvailable: [],
  },
};

/** Ordered list of the six named presets for card rendering. */
export const NAMED_PRESETS: Preset[] = ["blog", "corporate", "services", "ecommerce", "news", "saas"];

/**
 * Default custom overrides seeded when the user first selects Custom.
 * Mirrors the Blog/Content default family set so the user starts from a
 * coherent baseline rather than an empty scope.
 */
export const DEFAULT_CUSTOM_OVERRIDES: Record<string, boolean> = {
  // Legacy 3-boolean scope defaults to false (Blog baseline).
  protocols: false,
  account_auth: false,
  ecommerce: false,
  // Per-family keys mirror the Blog default set.
  feed_discovery: true,
  article_schema: true,
  localbusiness_schema: false,
  news_article_schema: false,
  claimreview_schema: false,
  product_offer_schema: false,
  commerce_fields: false,
  api_catalog: false,
  mcp: false,
  a2a: false,
  agent_skills: false,
  webmcp: false,
  oauth_oidc: false,
  ard_catalog: false,
  auth_md: false,
};