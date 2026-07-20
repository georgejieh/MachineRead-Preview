# machineread-audit

Public Agent Skill package for the free MachineRead Essentials audit. This skill teaches skills-compatible agents (Claude Code, Claude Desktop, IDE extensions) how to call the public MachineRead audit surface and how to read the response without overclaiming.

## What it does

- Triggers on requests to audit a public URL for AI visibility, agent accessibility, scrapability, or search-discovery readiness.
- Calls `POST https://api.machineread.ai/v1/audit` (reserved future production domain; not yet live) or the stdio MCP server (`run_essentials_audit`).
- Returns the 13-groups / 56-points / 30-40-30 Essentials contract.
- Reproduces the five mandatory caveats (no live ranking proof, no provider-IP authentication, no Firecrawl, no citation share, public-peer benchmark only).

## What it does not do

- Does not authenticate users, does not use API keys, does not touch paid tiers.
- Does not run paid crawlers, LLM-generated reports, or live ranking experiments.
- Does not expose benchmark profile data, audit caches, or customer submissions.
- Does not promise search ranking, traffic, backlinks, citation share, conversion, or field Core Web Vitals.

## Layout

```
skills/machineread-audit/
├── SKILL.md                # Skill entrypoint with Agent Skills spec frontmatter
├── README.md               # This file
├── references/
│   ├── api.md              # /v1/audit contract
│   ├── presets.md          # 7 presets and benchmark scope keys
│   ├── scoring-caveats.md  # 3 score systems, pillar caps, locked rows
│   ├── mcp-tools.md        # 4 MCP tools, stdio transport
│   ├── error-codes.md      # HTTP / helper / MCP error namespaces
│   └── examples.md         # 3 worked request/response samples
└── scripts/
    └── run_audit.py        # stdlib helper that calls POST /v1/audit
```

## License

This skill package is released under the **MIT License**. The MIT license applies only to the contents of `skills/machineread-audit/`. The parent MachineRead repository retains its own license (currently "no open-source license has been selected yet" — see the parent `README.md`); this skill package does not extend that license to the rest of the repository.

## Related docs

- `docs/api/index.md` — public HTTP API reference.
- `docs/api/mcp.md` — public MCP discovery reference.
- `docs/llms.txt` — product-level llms.txt for LLM consumers.
