# Examples

Three worked samples for the public MachineRead audit surface: minimal HTTP, preset, and custom with overrides.

> **Note:** The `https://api.machineread.ai` host in the examples below is a reserved future production domain and is not yet live. When production deploys, this is the canonical hostname. Until then, point calls at the local development URL `http://127.0.0.1:8000` (run the backend with `launch.bat` or `launch.sh`).

## 1. Basic HTTP audit (minimal URL)

```bash
curl -sS -X POST "https://api.machineread.ai/v1/audit" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/"}'
```

```json
{
  "api_version": "1.0",
  "url": "https://example.com/",
  "scope": {
    "include_protocols": false, "include_account_auth": false, "include_ecommerce": false,
    "label": "General website", "preset_applied": null, "overrides_applied": {},
    "included_families": [], "excluded_families": [],
    "machine_surfaces_scope": "common-contextual"
  },
  "overall_score": 42,
  "pillar_scores": { "off_site": 10, "scrapability": 20, "seo": 12 },
  "pillar_max": { "off_site": 30, "scrapability": 40, "seo": 30 },
  "agent_readiness": { "score": 88, "earned": 7, "max": 8, "label": "Developing agent-native readiness" },
  "benchmark": {
    "score": 42, "checked_score": 42, "checked_max": 56,
    "median_score": 42, "percentile": 50, "position_label": "At median",
    "basis": "Public fallback benchmark (snapshot 2026-07-17, blog preset).",
    "snapshot_date": "2026-07-17",
    "caveat": "Benchmark positions are relative context among public peers, not exposure proof."
  }
}
```

`overall_score` is the full-rubric number (0–100). `agent_readiness.max` is `8` (default scope; protocol, auth, and commerce are all off). With all three enabled the max is `21`. `benchmark.basis` carries the public fallback basis by snapshot date and preset. The five mandatory caveats from `SKILL.md` still apply. Equivalent helper call: `python ../scripts/run_audit.py https://example.com/ --field overall_score`.

## 2. Preset audit (saas)

```bash
curl -sS -X POST "https://api.machineread.ai/v1/audit" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://api.example.com/", "preset": "saas"}'
```

```json
{
  "api_version": "1.0",
  "url": "https://api.example.com/",
  "scope": {
    "include_protocols": true, "include_account_auth": true, "include_ecommerce": false,
    "label": "SaaS/Product/API audit", "preset_applied": "saas", "overrides_applied": {},
    "included_families": ["api_catalog", "mcp", "a2a", "agent_skills", "webmcp", "oauth_oidc", "ard_catalog", "auth_md"],
    "excluded_families": ["feed_discovery", "article_schema", "product_offer_schema", "commerce_fields"],
    "machine_surfaces_scope": "full"
  },
  "overall_score": 56,
  "pillar_scores": { "off_site": 12, "scrapability": 26, "seo": 18 },
  "pillar_max": { "off_site": 30, "scrapability": 40, "seo": 30 },
  "agent_readiness": { "score": 82, "earned": 14, "max": 17, "label": "Developing agent-native readiness" },
  "benchmark": {
    "score": 56, "checked_score": 56, "checked_max": 56,
    "median_score": 50, "percentile": 60, "position_label": "Above median",
    "basis": "Public fallback benchmark (snapshot 2026-07-17, saas preset).",
    "snapshot_date": "2026-07-17",
    "caveat": "Benchmark positions are relative context among public peers, not exposure proof."
  }
}
```

`scope` reflects the preset — `include_protocols` and `include_account_auth` are `true`; `include_ecommerce` stays `false`. `agent_readiness.max` is `17` because the protocol and auth dimensions are on but commerce is off. `benchmark.basis` carries the preset tag. Equivalent helper call: `python ../scripts/run_audit.py https://api.example.com/ --preset saas`.

## 3. Custom preset with overrides

```bash
curl -sS -X POST "https://api.machineread.ai/v1/audit" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://blog.example.com/", "preset": "custom", "custom_overrides": {"feed_discovery": true, "api_catalog": true}}'
```

```json
{
  "api_version": "1.0",
  "url": "https://blog.example.com/",
  "scope": {
    "include_protocols": true, "include_account_auth": false, "include_ecommerce": false,
    "label": "Custom/Power User audit", "preset_applied": "custom",
    "overrides_applied": { "feed_discovery": true, "api_catalog": true },
    "included_families": ["feed_discovery", "article_schema", "api_catalog"],
    "excluded_families": ["oauth_oidc", "auth_md", "ard_catalog", "mcp", "a2a", "agent_skills", "webmcp"],
    "machine_surfaces_scope": "full"
  },
  "overall_score": 49,
  "pillar_scores": { "off_site": 10, "scrapability": 22, "seo": 17 },
  "pillar_max": { "off_site": 30, "scrapability": 40, "seo": 30 },
  "agent_readiness": { "score": 71, "earned": 10, "max": 14, "label": "Developing agent-native readiness" },
  "benchmark": {
    "score": 49, "checked_score": 49, "checked_max": 56,
    "median_score": 47, "percentile": 55, "position_label": "Above median",
    "basis": "Public fallback benchmark (snapshot 2026-07-17, custom preset, p1_a0_c0).",
    "snapshot_date": "2026-07-17",
    "caveat": "Benchmark positions are relative context among public peers, not exposure proof."
  }
}
```

`preset: "custom"` is required when sending `custom_overrides`. Sending overrides with `preset: null` returns a 422. `overrides_applied` lists exactly which keys the user toggled on top of the Blog/Content default base. The benchmark `basis` carries the resolved scope key `p1_a0_c0` (protocol on, auth off, commerce off). The five mandatory caveats from `SKILL.md` still apply. Equivalent helper call: `python ../scripts/run_audit.py https://blog.example.com/ --preset custom --custom-overrides '{"feed_discovery": true, "api_catalog": true}'`.

## Failure sample (preset error)

For unknown preset errors, invalid URL handling, and rate-limit responses, see [`error-codes.md`](./error-codes.md) — invalid preset returns HTTP 422 with `detail` listing valid presets; helper exit code `1`.
