# MachineRead — AI Visibility & Agent Readiness Audit

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![Node.js](https://img.shields.io/badge/node-18%2B-green)](https://nodejs.org/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![CI](https://github.com/georgejieh/MachineRead-Preview/actions/workflows/ci.yml/badge.svg)](https://github.com/georgejieh/MachineRead-Preview/actions/workflows/ci.yml)

**MachineRead audits any public website for LLM discoverability, AI crawler access, and agent readiness.** It scans a URL the way machines actually consume it and returns a structured, deterministic report covering AI search optimization (GEO/AEO), scrapability, `llms.txt` and Markdown access, JSON-LD structured data, agent protocol surfaces (MCP, A2A, API catalogs), and the traditional SEO signals that AI retrieval still depends on. Everything runs locally, requires no API keys or accounts, and finishes in seconds.

This repository is the free Essentials tier of [MachineRead](https://machineread.ai) (reserved domain, production deployment planned): a FastAPI backend, a Next.js dashboard, and a benchmark system for comparing your site against real-world peers.

---

## Table of Contents

- [Why This Exists](#why-this-exists)
- [What Gets Checked](#what-gets-checked)
- [The Agent Readiness Score](#the-agent-readiness-score)
- [Benchmarks](#benchmarks)
- [Quick Start](#quick-start)
- [Manual Setup](#manual-setup)
- [API](#api)
- [What This Does Not Prove](#what-this-does-not-prove)
- [Machine-Readable Surfaces](#machine-readable-surfaces)
- [Project Structure](#project-structure)
- [License](#license)

## Why This Exists

Search behavior is moving from ten blue links to direct answers. ChatGPT, Claude, Perplexity, and Google's AI Overviews now sit between your website and a growing share of your audience, and behind them a second wave is arriving: autonomous agents that need to read, navigate, and act on your site without a human driving.

However, most advice about "AI SEO" stops at the correlation layer. Sites that get cited by LLMs tend to have good structured data, so the advice becomes "add structured data." That skips the question that actually matters: what is the mechanical path between your server and a model's answer?

MachineRead is built around tracing that path. For a model to cite you, a crawler has to be allowed in by your robots policy, fetch your page without hitting a bot challenge, find meaningful text in the raw HTML response (client-rendered app shells often serve none), parse your entities out of JSON-LD, and connect you to an off-site identity it already trusts. For an agent to act on your site, it additionally needs navigable semantic HTML, labelled controls, and ideally a machine-native surface such as `llms.txt`, an API catalog, or an MCP server card, due to the simple fact that an agent given a clean protocol endpoint will always prefer it over screen-scraping a JavaScript bundle. Every check in this tool maps to one concrete step in that chain. Meaning when a check fails, you know which specific link between your server and the model broke, and the report tells you what to change.

Two honest framing points before you run it. First, the claim is one-directional: passing these checks means machines can consume your site through the easiest available path; failing them means either the machines cannot, or your architecture serves the same need a different way (the tool reports what it observed, and does not pretend to adjudicate which). Second, a large portion of the rubric is deliberately unglamorous. Canonical tags, sitemaps, indexing directives, and crawl efficiency predate LLMs by two decades. The retrieval mechanics underneath AI search did not change nearly as much as the interface did, and as a result the boring fundamentals still carry real weight, which is exactly why they stay in the rubric.

## What Gets Checked

The Essentials audit runs 13 check groups worth a 56-point checked maximum, split across three pillars weighted 30/40/30. The middle pillar is the largest on purpose: scrapability and agent path-of-least-resistance is where AI consumption differs most from classic SEO.

| Pillar | Weight | Check groups |
| --- | --- | --- |
| **Off-site presence** | 30 | Social and entity metadata (Open Graph, Twitter cards, JSON-LD entity names, logo fields), Wikipedia and Wikidata entity evidence via cached public lookup. |
| **AI access & scrapability** | 40 | AI bot policy signals in robots.txt (search, training, and user-triggered crawlers), representative bot fetch access with challenge detection and browser-vs-bot routing differences, semantic HTML and agent navigation, JSON-LD structured data with scoped completeness, `llms.txt` and Markdown negotiation, raw HTML readability and app-shell detection, agent protocol discovery (Link headers, DNS-AID, API catalogs, OAuth metadata, MCP, A2A, WebMCP, Agent Skills). |
| **Traditional SEO** | 30 | Crawl efficiency and HTML performance, canonical and HTTPS hygiene, indexing directives (meta robots, `X-Robots-Tag`, snippet controls), search discovery hints (sitemap quality, sampled URL indexability, search-blurb coherence, hreflang, feeds, freshness). |

Every check is evidence-based against your live public responses. Essentials calls no paid search, backlink, social, crawler, or LLM APIs (the only external lookup is public Wikimedia, cached), so the free tier stays at zero marginal cost no matter how many audits you run.

Website presets (blog, corporate, services, ecommerce, news, SaaS, or fully custom) tune which optional check families run, so a personal blog is not penalized for lacking a product feed and a storefront is held to commerce schema expectations. The 13 core groups run for every preset, due to the fact that they measure things every website should have regardless of category.

## The Agent Readiness Score

Alongside the main rubric, the report includes a stricter agent-native lens: a probe count of machine-consumable surfaces an autonomous agent can discover without human help. The default scope runs 8 probes (robots publication, sitemap discovery, `llms.txt`, AI-specific robots rules, and related discovery hints). Enabling the protocol/API, account/auth, and commerce scopes expands it to 21 probes covering API catalogs, MCP server cards, A2A agent cards, Agent Skills indexes, WebMCP manifests, ARD static catalogs, and auth metadata.

Most sites score low here today. That is expected, and it is also the point: these surfaces are cheap to publish, almost nobody publishes them yet, and an agent that finds one will take it every time. In other words, this is the score where early movers get the largest relative gain for the smallest effort.

## Benchmarks

Scores without context are just numbers, so the report compares your result against peer profiles of real organizations across categories (blog, corporate, services, ecommerce, news, SaaS) and size bands (boutique, specialty, enterprise). Benchmark profiles are stored per-check rather than as pre-baked aggregates: each peer is captured once at full scope, and its score is recomputed at request time against exactly the scope you selected. As a result your 14-probe custom audit is compared against what those peers earned on the same 14 probes, and never against a mismatched denominator.

The public tree ships a sample peer list in `scripts/benchmark_peers.sample.json`. To generate a fresh snapshot from live captures, run:

```bash
./scripts/update_benchmarks.sh          # macOS/Linux
scripts\update_benchmarks.bat           # Windows
```

This fetches each peer at full scope and writes the profile JSON to `backend/private_data/benchmark_profiles.json`. The refresh is deliberate and on-demand (the launch scripts never trigger it), so benchmark freshness in your checkout is yours to control.

## Quick Start

Double-click `launch.bat` (Windows) or run `./launch.sh` (macOS/Linux). The script checks for Python 3.11+ and Node.js 18+, installs dependencies, starts the backend on port 8000 and the dashboard on port 3000, and opens your browser. Submit a public URL, pick a preset, and read the report. No API keys, no accounts, no telemetry.

## Manual Setup

Prerequisites: Python 3.11+ and Node.js 18+.

```bash
# Backend
python -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt        # Windows: backend\.venv\Scripts\pip
backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --port 8000

# Frontend (second shell)
npm --prefix frontend install
npm --prefix frontend run dev -- --port 3000
```

Open `http://localhost:3000`. No environment configuration is required.

## API

The audit is a stateless API you can call directly:

- `POST /v1/audit` — full Essentials audit for one public HTTP(S) URL, with per-check evidence, findings, and fix guidance. Accepts a `preset` or granular scope options.
- `POST /v1/audit/summary` — the same canonical pipeline returning a compact deterministic summary built for agent consumption: scores, denominators, benchmark context, and up to five attention rows, without the full prose.
- `GET /health` — liveness check.

All audit routes share one per-client-IP rate limit bucket. Requests are SSRF-guarded: URLs resolving to private, loopback, link-local, or otherwise non-global addresses are rejected, and every redirect hop is re-validated. The full contract lives in [`docs/api/`](docs/api/) including the OpenAPI schema.

## What This Does Not Prove

Essentials reports observable public signals. It does not prove search ranking, organic traffic, backlinks, social traction, field Core Web Vitals, model citation share, live index coverage, or conversion performance, and comparable benchmark scores do not imply comparable traffic or brand authority. The user-agent probes originate from MachineRead and do not authenticate provider IPs, so a site that treats unverified crawlers differently from verified ones may be scored on the stricter path (which is itself information about how that site treats unknown agents).

Nine advanced rows (earned mentions, social traction, extraction fidelity, multi-engine index coverage, Core Web Vitals, keyword gap, AI citation share, agent task simulation, and owned social presence) appear in the report as locked tier metadata. The free audit reports them as unavailable rather than implying they were measured.

## Machine-Readable Surfaces

This project practices what it audits. The repository publishes its own agent-consumable surfaces, which double as working examples of the formats the audit checks for:

| Surface | Location | Format |
| --- | --- | --- |
| OpenAPI schema | [`docs/api/openapi.json`](docs/api/openapi.json) | OpenAPI 3.1 |
| `llms.txt` | [`docs/llms.txt`](docs/llms.txt) | llms.txt |
| API catalog | [`docs/api/api-catalog.json`](docs/api/api-catalog.json) | RFC 9264 linkset |
| ARD catalog | [`docs/api/ai-catalog.json`](docs/api/ai-catalog.json) | Agentic Resource Discovery v0.9 |
| MCP server card | [`docs/api/mcp-server-card.json`](docs/api/mcp-server-card.json) | MCP discovery metadata |
| Agent Skill | [`skills/machineread-audit/`](skills/machineread-audit/) | Agentskills.io package |

If you are deciding which of these to publish on your own site, copying the ones here is a reasonable place to start.

## Project Structure

```
backend/         FastAPI audit API, scoring rubric, checks, benchmarks, tests
frontend/        Next.js 15 dashboard, score rings, findings, benchmark explorer
docs/            Public machine-readable surfaces (OpenAPI, llms.txt, catalogs)
scripts/         Benchmark refresh tooling and sample peer list
skills/          Public Agent Skill package
launch.sh/.bat   One-command local launchers
```

CI runs backend tests and frontend type checks on every push.

## License

MIT. See [LICENSE](LICENSE).
