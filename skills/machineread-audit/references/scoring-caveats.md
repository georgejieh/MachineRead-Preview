# Scoring and Caveats

The Essentials response carries three independent score systems, one pillar-cap split, and nine locked advanced rows. This reference explains what each number means and which caveats apply.

## Three score systems

### `overall_score`

The full-rubric score (0–100). Includes the 10 universal core rows, `social`, `wikipedia`, and the 9 locked advanced rows reported as unavailable. The `overall_score` is the user-facing headline number; it is **not** a citation-share, traffic, search-ranking, social-traction, conversion, or field Core Web Vitals measurement.

### `benchmark.score`

The Essentials evidence score computed from included free rows only. This is the value used in the public benchmark comparison. Locked/paid rows are excluded from this denominator.

### `agent_readiness.score`

A stricter agent-native lens. Counts only the explicit agent-readiness signals (robots, `llms.txt`, machine surfaces, AI bot rules, A2A, OAuth discovery, MCP, Agent Skills, WebMCP, ARD, `auth.md`, x402, MPP, UCP, ACP). The denominator expands with the resolved scope:

- **8 probes** — default scope (no protocol, auth, or commerce dimension).
- **21 probes** — full scope (protocol + auth + commerce all enabled). The full scope is the maximum and matches the formula: default 8 + protocols 6 + account/auth 3 + commerce 4 = 21.

Three rows overlap with strict agent-readiness probes (`robots_txt`, `llms_txt`, `machine_surfaces`); the overlap is preserved in the report copy.

## 13 included check groups

Essentials returns 13 rows with a 56-point checked maximum when the default blog scope is applied:

| Check group                          | Pillar        | Max |
| ------------------------------------ | ------------- | ---: |
| AI Bot Policy Signals                | `scrapability`| 6    |
| MachineRead Bot Fetch Access         | `scrapability`| 6    |
| Semantic HTML & Agent Navigation     | `scrapability`| 4    |
| JSON-LD Structured Data              | `scrapability`| 5    |
| LLM Text & Markdown Access           | `scrapability`| 5    |
| Raw HTML Readability                 | `scrapability`| 4    |
| Agent Protocol Discovery             | `scrapability`| 3    |
| Crawl Efficiency & HTML Performance  | `seo`         | 3    |
| Canonical & HTTPS                    | `seo`         | 5    |
| Indexing Directives                  | `seo`         | 5    |
| Search Discovery Hints               | `seo`         | 4    |
| Social & Entity Metadata             | `off_site`    | 2    |
| Wikipedia & Wikidata Entity          | `off_site`    | 4    |

Total: 56 checked points across the 30/40/30 pillar caps.

## 9 locked advanced rows

These rows are reported as `state: "locked"` and **never** contribute to the free score.

| Advanced row                    | Tier label |
| ------------------------------- | ---------- |
| Earned mentions and backlinks   | Starter    |
| Owned social presence           | Starter    |
| Social traction and reviews     | Starter    |
| Extraction fidelity             | Starter    |
| Multi-engine index coverage     | Starter    |
| Core Web Vitals                 | Starter    |
| Keyword and competitor gap      | Starter    |
| AI citation share               | Pro        |
| Agent task simulation           | Pro        |

## 30/40/30 pillar caps

- `off_site`: max 30 points.
- `scrapability`: max 40 points.
- `seo`: max 30 points.

These caps are reflected in `pillar_max` and enforced in the scoring model. A pillar can never exceed its cap.

## 21-probe strict agent-readiness full scope

When `protocol`, `account_auth`, and `commerce` dimensions are all enabled, the strict agent-readiness surface list expands to its full scope:

- 8 default probes (robots, sitemap, Link headers, DNS-AID, Markdown negotiation, AI bot rules, Content Signals, Web Bot Auth).
- **Protocol probes (6):** API Catalog, MCP Server Card, A2A Agent Card, Agent Skills index, WebMCP manifest, ARD static catalog.
- **Account/auth probes (3):** OAuth/OIDC discovery metadata, OAuth Protected Resource metadata, `auth.md`.
- **Commerce probes (4):** x402, MPP, UCP, ACP.

Total: 8 + 6 + 3 + 4 = 21. The probe list and the formula are part of the MachineRead runtime contract; the contract is the same across HTTP and MCP.

## Five mandatory caveats

Reproduce these caveats in any user-facing copy that surfaces a score:

1. Free Essentials does **not** verify live DuckDuckGo or Bing ranking.
2. Free Essentials does **not** authenticate provider IP ranges.
3. Free Essentials does **not** call Firecrawl or any paid crawler.
4. Scores are relative public-readiness signals, not citation share, traffic, search ranking, social traction, conversion, or field Core Web Vitals.
5. Benchmark position is among public peers; comparable scores do not imply comparable real-world outcomes.

## State values per row

Every `CheckResult` row carries a `state`:

- `pass` — row met the threshold.
- `partial` — row met part of the threshold.
- `fail` — row did not meet the threshold.
- `warn` — row could not complete during the audit. Treat as **inconclusive**, not as verified evidence.
- `locked` — paid/private coverage not exposed via the free API.

## Public vs private benchmark

The public benchmark uses public-safe fallback profiles. Production benchmark profiles are private and intentionally not exposed through the free API. When the resolved scope has no matching public peer profile, the response either hides the benchmark comparison or labels the nearest available proxy in the `basis` field with a clear caveat.
