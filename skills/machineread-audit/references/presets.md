# Presets

Seven presets cover the supported free website categories. Each preset is a deterministic identifier; the backend resolves the identifier to a fixed set of eligible surfaces, strict agent-readiness probes, included and excluded surface copy, and a benchmark scope key.

Source of truth: the preset identifiers, scope flags, and benchmark scope keys described in this document are authoritative for the public Agent Skill package.

## Preset table

| Preset | Audience | Default optional surfaces | machine_surfaces scope | Benchmark scope key |
| --- | --- | --- | --- | --- |
| `blog` | Personal blogs, content sites, newsletters | None (article schema, feeds, speakable already on) | common-contextual only | `p0_a0_c0` |
| `corporate` | Company sites, brand pages, portfolios | None | common-contextual only | `p0_a0_c0` |
| `services` | Local businesses, service providers, clinics | None (local-business schema already on) | common-contextual only | `p0_a0_c0` |
| `ecommerce` | Online stores, product catalogs, marketplaces | `protocol + auth + commerce` | full protocol scope | `p1_a1_c1` |
| `news` | News sites, magazines, publishers | None (news schema, ClaimReview already on) | common-contextual only | `p0_a0_c0` |
| `saas` | SaaS platforms, API products, developer tools | `protocol + auth` | full protocol scope | `p1_a1_c0` |
| `custom` | Expert mode; user-selected sub-families | User-selected via `custom_overrides` | User-controlled | depends on overrides |

Benchmark scope keys follow `p{protocol}_a{auth}_c{commerce}` where each flag is `0` or `1`. The public benchmark comparison picks the peer profile whose scope key matches the resolved scope; if no exact peer exists the comparison is hidden or labelled as a nearest available proxy.

## What each preset enables

### blog

Always-on: 13 included Essential rows (10 universal core + 3 contextual: `social`, `wikipedia`, `machine_surfaces`). Per-preset families: Article/BlogPosting JSON-LD, RSS/Atom feeds, speakable (tracked-only), author/taxonomy, hreflang. Excludes API catalog, MCP, A2A, Agent Skills, WebMCP, OAuth discovery, commerce metadata.

### corporate

Always-on: 13 included Essential rows (10 universal core + 3 contextual: `social`, `wikipedia`, `machine_surfaces`). Per-preset families: Organization schema, social profile links, Wikipedia entity (`sameAs`), trust pages. Excludes article, local-business, commerce, protocol/auth surfaces.

### services

Always-on: 13 included Essential rows (10 universal core + 3 contextual: `social`, `wikipedia`, `machine_surfaces`). Per-preset families: LocalBusiness schema (name, address, telephone, opening hours, geo, area served), trust pages, contact surfaces. No standalone maps or reviews requirement in free Essentials.

### ecommerce

Always-on: 13 included Essential rows (10 universal core + 3 contextual: `social`, `wikipedia`, `machine_surfaces`). Per-preset families: Product/Offer/MerchantReturnPolicy schema (SKU/GTIN, brand, price, price-validity, shipping, returns), commerce metadata (x402, MPP, UCP, ACP), catalog JSON, Returns/Shipping trust-page discovery. Bundles `protocol + auth + commerce` by default. Excludes nothing — full-scope preset.

### news

Always-on: 13 included Essential rows (10 universal core + 3 contextual: `social`, `wikipedia`, `machine_surfaces`). Per-preset families: NewsArticle schema, ClaimReview, BreadcrumbList, RSS/Atom, speakable (tracked-only), hreflang, dated urgency, NewsMediaOrganization hints. Excludes ecommerce, account/auth, API-protocol defaults.

### saas

Always-on: 13 included Essential rows (10 universal core + 3 contextual: `social`, `wikipedia`, `machine_surfaces`). Per-preset families: OpenAPI/API catalog, MCP, A2A, Agent Skills, WebMCP, ARD catalog (`/.well-known/ai-catalog.json`, robots `Agentmap`, `rel="ai-catalog"`, DNS `_catalog._agents` / `_search._agents`), conventional path probes for `/api` and `/docs`. Bundles `protocol + auth` by default. Excludes ecommerce catalog rows.

### custom

Always-on: 13 included Essential rows (10 universal core + 3 contextual: `social`, `wikipedia`, `machine_surfaces`). Default base preset: `blog`. Users may override individual sub-families via `custom_overrides`. Impossible combinations are rejected at the API boundary (see `api.md`).

## How to pick a preset

- **Storefront or catalog** → `ecommerce`.
- **API product, SaaS, or developer tool** → `saas`.
- **News publisher or magazine** → `news`.
- **Local business or service provider** → `services`.
- **Blog, content site, or personal site** → `blog`.
- **Company homepage, brand page, or portfolio** → `corporate`.
- **Unsure** → default to `corporate` (smallest universal core with no implicit protocol/auth/commerce assumptions).
- **Explicit sub-family control** → `custom` with `custom_overrides`.

## Preset vs legacy booleans

The three legacy booleans (`include_protocols`, `include_account_auth`, `include_ecommerce`) remain supported for backward compatibility. They resolve to the same family list as before. New integrations should use the preset path:

- The preset is **required** to use `custom_overrides`. Sending overrides with `preset=null` returns a 422.
- The preset **wins** over the three booleans when both are present.
- The booleans remain useful for callers that already encode their scope as flags and do not want to migrate.

## Caveats

- The preset maps to the resolved scope and benchmark scope key. Mismatching the preset and the audience description does not change the public output shape, only the family list and the benchmark comparison.
- `custom` requires the user to know which families they want. The validator rejects unknown keys, locked/paid rows, universal core rows, and impossible combinations. See `api.md` for the request validation contract.
- The 10 universal core rows plus the 3 contextual rows (`social`, `wikipedia`, `machine_surfaces`) form the 13 included Essentials rows and cannot be disabled by any preset or override.
- Locked/paid rows always appear with `state: "locked"` and are excluded from the Essentials benchmark denominator.
