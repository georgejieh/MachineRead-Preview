# MachineRead — Free Website Audit Tool

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![Node.js](https://img.shields.io/badge/node-18%2B-green)](https://nodejs.org/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![CI](https://github.com/georgejieh/MachineRead-Preview/actions/workflows/ci.yml/badge.svg)](https://github.com/georgejieh/MachineRead-Preview/actions/workflows/ci.yml)

MachineRead audits a public website URL for AI visibility, agent accessibility,
scrapability, and search discovery readiness. This repository contains the free
Essentials audit: a local scanner, a FastAPI backend, and a Next.js dashboard
for reviewing structured findings.

## Quick Start

Double-click `launch.bat` (Windows) or run `launch.sh` (macOS/Linux). Both
scripts check Python and Node.js, install dependencies, start the backend on
port 8000 and frontend on port 3000, and open the dashboard in your browser.
No API keys, accounts, or paid services are required.

This project is intentionally evidence-based. Essentials checks public HTTP,
DNS, robots, sitemap, HTML, metadata, structured data, and machine-readable
agent surfaces without calling paid search, backlink, social, crawler, or LLM
APIs.

## What Essentials Checks

Essentials returns 13 included check groups across three pillars with a
56-point checked maximum:

| Pillar | Check group | What it looks for |
| --- | --- | --- |
| Off-site | Social & Entity Metadata | Homepage-published profile hints, Open Graph/Twitter metadata, JSON-LD entity names, logo/image fields, and public Wikimedia entity lookup. |
| Off-site | Wikipedia & Wikidata Entity | Cached public Wikipedia/Wikidata entity evidence with refusal-aware lookup handling. |
| AI access | AI Bot Policy Signals | Robots.txt access and policy for major search, AI, training, user-triggered, and crawler user agents. |
| AI access | MachineRead Bot Fetch Access | Representative bot fetches with serialized probes and 429 retry, access friction, challenge signatures, and inferred browser-vs-bot routing differences. |
| AI access | Semantic HTML & Agent Navigation | Headings, semantic landmarks, crawlable navigation, labelled controls, form affordances, and image alt coverage. |
| AI access | JSON-LD Structured Data | JSON-LD syntax, selected schema types, and scoped completeness for commerce, software, articles, breadcrumbs, FAQ, and organization data. |
| AI access | LLM Text & Markdown Access | `llms.txt`, Markdown negotiation, root Markdown fallbacks, meaningful text bodies, and related discovery hints. |
| AI access | Raw HTML Readability | Initial HTML readability, extractable words, script/style weight, tighter app-shell detection, and JS rendering risk. |
| AI access | Agent Protocol Discovery | Link headers, DNS-AID hints, API catalogs, OAuth metadata, MCP/A2A/WebMCP/Agent Skill surfaces, auth metadata, and scoped commerce metadata. |
| SEO | Crawl Efficiency & HTML Performance | Response timing, cache validators, cache policy, crawler-size risk, render-blocking assets, unsized media, sync head scripts, and mobile viewport basics. |
| SEO | Canonical & HTTPS | HTTPS redirects, canonical self-reference, and duplicate www/non-www surface checks. |
| SEO | Indexing Directives | Meta robots, `X-Robots-Tag`, snippet controls, image/video preview directives, crawler-specific rules, and unavailable-after hints. |
| SEO | Search Discovery Hints | Sitemap discovery and quality, sampled URL accessibility/indexability, page metadata, trust surfaces, search-blurb coherence, hreflang, feeds, and update freshness. |

The user-agent probes originate from MachineRead and do not authenticate
provider IPs. `DuckAssistBot` is a real-time AI-assisted retrieval crawler,
not a training crawler; blocking it does not prove an effect on organic
ranking or result inclusion.

Search Discovery Hints also checks deterministic page-owned search-blurb
coherence across titles, meta descriptions, headings, main content, canonical
URLs, and social descriptions. It does not verify live DuckDuckGo or Bing
snippet selection, display, ranking, or index coverage. Local source-response
extraction-readiness evidence reuses bounded HTML, Markdown, `llms.txt`,
sitemap, JSON-LD, and commerce signals. It does not call Firecrawl or another
provider API and does not verify browser rendering, provider output,
crawl/map/screenshots, extraction fidelity, or actual product extraction.

The dashboard separates three score concepts:

- `overall_score`: the full 100-point rubric, including locked advanced rows as
  unavailable until verified.
- `benchmark.score`: the Essentials evidence score from included free rows only.
- `agent_readiness.score`: a stricter agent-native lens with an 8-probe default
  scope and a 21-probe full scope when protocol/API, account/auth, and commerce
  options are enabled.

Scope options let users include or exclude protocol/API, account/auth, and
commerce expectations before a scan runs, so a general website is not penalized
for irrelevant surfaces.

### Benchmarks

Peer benchmark profiles are per-check rather than per-scope: each peer is
captured once at full scope, then its score is recomputed at request time from
your chosen scope's included checks and probes. The bundled sample in
`_SAMPLE_BENCHMARK_SEEDS` is fictional and ships with the public tree for the
demo experience. For a real benchmark snapshot, run
`python scripts/refresh_benchmarks.py --peers scripts/benchmark_peers.sample.json --out backend/private_data/benchmark_profiles.json`
from the repo root, which will fetch each peer at full scope and write the
v2 profile JSON. The public tree ships sample seeds; the private deploy
updates the real snapshot monthly.

## Limitations

Essentials does not prove real search ranking, search traffic, backlinks,
social traction, field Core Web Vitals, model citation share, or conversion
success. It reports observable public signals and relative benchmark context,
not guaranteed exposure.

Benchmarks are relative context generated under matching or nearest-available
scope assumptions. Comparable scores do not imply comparable search traffic,
model mentions, ecommerce conversion, or brand authority.

Advanced rows are public metadata only in this repository:

| Advanced row | Tier label |
| --- | --- |
| Earned mentions and backlinks | Starter |
| Owned social presence | Starter |
| Social traction and reviews | Starter |
| Extraction fidelity | Starter |
| Multi-engine index coverage | Starter |
| Core Web Vitals | Starter |
| Keyword and competitor gap | Starter |
| AI citation share | Pro |
| Agent task simulation | Pro |

The free audit reports those rows as unavailable rather than implying
they were measured.

## Local Setup (Manual)

Prerequisites: Python 3.11+ and Node.js 18+.

```powershell
# Backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt

# Frontend
npm --prefix frontend install
```

No environment configuration is required to run Essentials locally. The
Wikimedia lookup uses public access with caching.

Start the backend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --port 8000
```

Start the frontend in a second shell:

```powershell
npm --prefix frontend run dev -- --port 3000
```

Open `http://localhost:3000`, submit a public URL, and review the report.

## Project Structure

```
backend/         FastAPI audit API, scoring, checks, benchmarks, and tests.
frontend/        Next.js dashboard, API client, components, and TypeScript types.
docs/            Public agent-consumable surface.
  api/           Public API reference, OpenAPI schema, ARD catalog, MCP card.
  llms.txt       Product-level llms.txt for LLM consumers.
skills/          Public Agent Skill package.
  machineread-audit/
launch.bat       One-click Windows launcher.
launch.sh        One-click macOS/Linux launcher.
LICENSE          MIT license.
```

## Agent-Consumable Surfaces

MachineRead exposes machine-readable surfaces for agents:

- **OpenAPI** (`docs/api/openapi.json`): Full API specification with models and examples.
- **Agent Skill** (`skills/machineread-audit/`): Agentskills.io-compliant skill
  package that describes how to invoke the free audit API programmatically.
- **API Catalog** (`docs/api/api-catalog.json`): RFC-9264 linkset for structured
  API discovery.
- **ARD Catalog** (`docs/api/ai-catalog.json`): Agentic Resource Discovery
  v0.9 static catalog for registry-independent machine discovery.
- **llms.txt** (`docs/llms.txt`): LLM-oriented product-level content for models
  that consume text-based page representations.
- **MCP Server Card** (`docs/api/mcp-server-card.json`): Public MCP discovery
  metadata for the MachineRead MCP server.

## Verification

The CI workflow (`.github/workflows/ci.yml`) runs backend Python compile,
backend tests, and frontend TypeScript checks on every push.

## License

MIT. See [LICENSE](LICENSE) for the full text.