"""Public audit service shared by main.py and MCP server.

This module extracts reusable audit logic from the FastAPI app into a callable
async service layer. It preserves all API contracts, SSRF validation, error
handling, and scoring semantics without requiring HTTP context.
"""

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Literal

from app.agent_readiness import build_agent_readiness_summary, _scope_probe_labels
from app.audit_context import build_audit_context
from app.benchmarks import build_agent_benchmark_comparison
from app.checks.bot_access import check_bot_access
from app.checks.canonical import check_canonical
from app.checks.html_structure import check_html_structure
from app.checks.indexing import check_indexing
from app.checks.llms_txt import check_llms_txt
from app.checks.machine_surfaces import check_machine_surfaces
from app.checks.pagespeed import check_pagespeed
from app.checks.robots import check_robots
from app.checks.schema_ld import check_schema_ld
from app.checks.search_discovery import check_search_discovery
from app.checks.social import check_social
from app.checks.ssr import check_ssr
from app.checks.wikipedia import check_wikipedia
from app.essential_runner import (
    _agent_readiness_max,
    _fallback_agent_readiness_summary,
    _fallback_check_result,
    _safe_agent_readiness_summary,
    _safe_check,
)
from app.models import (
    AgentReadinessSummary,
    AuditResult,
    CheckResult,
)
from app.presets import VALID_PRESETS, ResolvedScope, _resolve_legacy, resolve_scope
from app.fetch_evidence import collect_fetch_evidence
from app.rubric import ESSENTIALS_CHECK_GROUPS, EssentialsCheckGroup
from app.scoring import build_result
from app.ssrf import validate_url


_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditRunConfig:
    """Configuration for a single audit run.

    Attributes:
        url: The target URL to audit (normalized at entry).
        preset: Optional preset name for scope selection.
        custom_overrides: Optional override dict for custom/power-user mode.
    """

    url: str
    preset: str | None = None
    custom_overrides: dict[str, bool] | None = None


@dataclass(frozen=True)
class StructuredOk:
    """Structured OK response wrapper.

    This shape matches the MCP tool output contract: ok is always a boolean,
    data is present when ok is True, and error is absent.
    """

    data: dict
    ok: Literal[True] = True

    def to_dict(self) -> dict:
        return {"ok": True, "data": self.data}


@dataclass(frozen=True)
class StructuredError:
    """Structured error response wrapper.

    This shape matches the MCP tool output contract: ok is always False,
    error is present with code and message, and data is absent.
    """

    code: str
    message: str
    ok: Literal[False] = False

    def to_dict(self) -> dict:
        return {"ok": False, "error": {"code": self.code, "message": self.message}}


AuditResultType = StructuredOk | StructuredError


class RateLimiter:
    """Fixed-window rate limiter with per-window counters.

    Tracks requests per client identifier using a fixed time window.
    Default rate: 3/minute (same as main.py default).
    """

    def __init__(self, requests: int = 3, window_seconds: int = 60):
        if requests <= 0:
            raise ValueError("requests must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._max_requests = requests
        self._window = window_seconds
        self._windows: dict[str, tuple[int, int]] = {}

    def is_allowed(self, client_id: str) -> tuple[bool, int, int]:
        """Check if client is within rate limit.

        Returns:
            Tuple of (allowed: bool, remaining: int, reset_timestamp: int)
        """
        now = int(time.time())
        window_start = now // self._window * self._window

        current_window, count = self._windows.get(client_id, (0, 0))

        if current_window != window_start:
            current_window = window_start
            count = 0

        if count >= self._max_requests:
            reset_at = current_window + self._window
            _log.warning("rate limit exceeded: client=%s", client_id)
            return False, 0, reset_at

        return True, self._max_requests - count - 1, current_window + self._window

    def record(self, client_id: str) -> None:
        """Record a request for the client."""
        now = int(time.time())
        window_start = now // self._window * self._window

        current_window, count = self._windows.get(client_id, (0, 0))
        if current_window != window_start:
            current_window = window_start
            count = 0

        self._windows[client_id] = (current_window, count + 1)


class AuditCache:
    """LRU cache for audit results keyed by canonical URL + preset.

    Bounded OrderedDict with maxsize 32. Stores only successful completed audits.
    """

    def __init__(self, maxsize: int = 32):
        if maxsize < 1:
            raise ValueError("maxsize must be at least 1")
        self._cache: OrderedDict[str, AuditResult] = OrderedDict()
        self._maxsize = maxsize

    def _key(
        self,
        url: str,
        preset: str | None,
        overrides: dict | None = None,
    ) -> str:
        """Generate cache key from normalized URL, preset, and overrides.

        When ``overrides`` is non-empty, the canonical JSON serialization
        (sort_keys=True) is folded into the hash so that two runs with the
        same URL/preset but different override dicts produce distinct keys.
        """
        canonical = url.rstrip("/").lower()
        preset_part = preset or "none"
        overrides_part = ""
        if overrides:
            overrides_part = json.dumps(overrides, sort_keys=True)
        payload = f"{canonical}:{preset_part}:{overrides_part}"
        return hashlib.sha256(payload.encode()).hexdigest()

    def get(
        self,
        url: str,
        preset: str | None,
        overrides: dict | None = None,
    ) -> AuditResult | None:
        """Lookup audit result by URL, preset, and overrides."""
        key = self._key(url, preset, overrides)
        if key in self._cache:
            self._cache.move_to_end(key)
            _log.debug("cache hit: id=%s", key)
            return self._cache[key]
        return None

    def put(
        self,
        url: str,
        preset: str | None,
        result: AuditResult,
        overrides: dict | None = None,
    ) -> None:
        """Store audit result; evicts oldest if at capacity."""
        key = self._key(url, preset, overrides)
        if key in self._cache:
            self._cache.move_to_end(key)
        will_evict = len(self._cache) >= self._maxsize and key not in self._cache
        self._cache[key] = result
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)
            if will_evict:
                _log.debug("cache eviction at capacity=%s", self._maxsize)

    def get_by_audit_id(self, audit_id: str) -> AuditResult | None:
        """Lookup by audit_id (which is the cache key)."""
        if audit_id in self._cache:
            self._cache.move_to_end(audit_id)
            return self._cache[audit_id]
        return None


class AuditService:
    """Public service layer for MachineRead audit operations.

    Encapsulates validation, rate limiting, caching, and audit orchestration
    without FastAPI dependencies. Reusable by both HTTP and MCP entrypoints.
    """

    def __init__(
        self,
        rate_limit: int = 3,
        rate_window: int = 60,
        cache_size: int = 32,
    ):
        self._rate_limiter = RateLimiter(rate_limit, rate_window)
        self._cache = AuditCache(cache_size)
        self._ESSENTIAL_GROUPS_BY_NAME = {
            group.check_name: group for group in ESSENTIALS_CHECK_GROUPS
        }

    def _essential_group(self, check_name: str) -> EssentialsCheckGroup:
        return self._ESSENTIAL_GROUPS_BY_NAME[check_name]

    def _agent_readiness_max(
        self,
        include_protocols: bool,
        include_account_auth: bool,
        include_ecommerce: bool,
    ) -> int:
        return _agent_readiness_max(
            include_protocols, include_account_auth, include_ecommerce
        )

    def _fallback_check_result(self, group: EssentialsCheckGroup) -> CheckResult:
        return _fallback_check_result(group)

    async def _safe_check(
        self, group: EssentialsCheckGroup, check: Awaitable[CheckResult]
    ) -> CheckResult:
        return await _safe_check(group, check, logger=None)

    async def _safe_agent_readiness_summary(
        self,
        context,
        include_protocols: bool,
        include_account_auth: bool,
        include_ecommerce: bool,
    ) -> AgentReadinessSummary:
        return await _safe_agent_readiness_summary(
            context,
            include_protocols,
            include_account_auth,
            include_ecommerce,
            logger=None,
        )

    def _fallback_agent_readiness_summary(
        self,
        include_protocols: bool,
        include_account_auth: bool,
        include_ecommerce: bool,
    ) -> AgentReadinessSummary:
        return _fallback_agent_readiness_summary(
            include_protocols, include_account_auth, include_ecommerce
        )

    async def _build_context_or_error(
            self, url: str
        ) -> tuple[Any | None, StructuredError | None]:
        try:
            ctx = await build_audit_context(url)
            return ctx, None
        except Exception as exc:
            return None, StructuredError(
                code="AUDIT_SETUP_FAILED",
                message=f"Could not start audit for {url}: {exc}",
            )

    async def _resolve_scope(self, config: AuditRunConfig) -> ResolvedScope:
        """Resolve scope from preset or defaults."""
        if config.preset is not None:
            return resolve_scope(config.preset, config.custom_overrides)
        return _resolve_legacy(False, False, False)

    async def _run_essential_checks(
        self, context, scope: ResolvedScope
    ) -> list[CheckResult]:
        include_ecommerce = scope.include_ecommerce

        try:
            qa2_evidence = await collect_fetch_evidence(context, include_ecommerce)
        except Exception:
            qa2_evidence = None

        checks = await asyncio.gather(
            self._safe_check(
                self._essential_group("robots_txt"), check_robots(context)
            ),
            self._safe_check(
                self._essential_group("bot_access"), check_bot_access(context)
            ),
            self._safe_check(
                self._essential_group("html_structure"),
                check_html_structure(context),
            ),
            self._safe_check(
                self._essential_group("schema_ld"),
                check_schema_ld(context, include_ecommerce, qa2_evidence),
            ),
            self._safe_check(
                self._essential_group("llms_txt"),
                check_llms_txt(context, qa2_evidence),
            ),
            self._safe_check(
                self._essential_group("ssr"), check_ssr(context, qa2_evidence)
            ),
            self._safe_check(
                self._essential_group("machine_surfaces"),
                check_machine_surfaces(
                    context,
                    scope.include_protocols,
                    scope.include_account_auth,
                    include_ecommerce,
                ),
            ),
            self._safe_check(
                self._essential_group("pagespeed"), check_pagespeed(context)
            ),
            self._safe_check(
                self._essential_group("canonical"), check_canonical(context)
            ),
            self._safe_check(
                self._essential_group("indexing"), check_indexing(context)
            ),
            self._safe_check(
                self._essential_group("search_discovery"),
                check_search_discovery(context, include_ecommerce, qa2_evidence),
            ),
            self._safe_check(
                self._essential_group("social"), check_social(context)
            ),
            self._safe_check(
                self._essential_group("wikipedia"), check_wikipedia(context)
            ),
        )
        return list(checks)

    async def run_audit(
        self,
        config: AuditRunConfig,
        client_id: str = "default",
    ) -> AuditResultType:
        """Execute a full Essentials audit.

        Flow:
        1. Validate URL length <= 2048
        2. SSRF validate_url for safety
        3. Resolve preset/scope
        4. Check cache; cache hits skip the rate-limit charge 
        5. Check rate limits (only on cache miss)
        6. Build audit context
        7. Run essential checks
        8. Build final result
        9. Cache and return
        """
        _log.debug("audit start: client=%s preset=%s", client_id, config.preset)

        if len(config.url) > 2048:
            _log.error("audit failed: code=URL_TOO_LONG")
            return StructuredError(
                code="URL_TOO_LONG",
                message="URL exceeds maximum length of 2048 characters",
            )

        normalized_url = config.url
        if not normalized_url.startswith(("http://", "https://")):
            normalized_url = f"https://{normalized_url}"

        try:
            validate_url(normalized_url)
        except ValueError as exc:
            _log.error("audit failed: code=URL_NOT_ALLOWED")
            return StructuredError(
                code="URL_NOT_ALLOWED",
                message=f"URL not allowed: {exc}",
            )

        config = AuditRunConfig(
            url=normalized_url,
            preset=config.preset,
            custom_overrides=config.custom_overrides,
        )

        if config.preset is not None and config.preset not in VALID_PRESETS:
            _log.error("audit failed: code=INVALID_PRESET")
            return StructuredError(
                code="INVALID_PRESET",
                message=(
                    f"Unknown preset {config.preset!r}; valid presets: "
                    f"{sorted(VALID_PRESETS)}"
                ),
            )

        try:
            scope = await self._resolve_scope(config)
        except (KeyError, ValueError) as exc:
            _log.error("audit failed: code=INVALID_PRESET")
            return StructuredError(code="INVALID_PRESET", message=str(exc))

        # check the cache BEFORE charging the rate limiter. Cache hits
        # serve the same result without consuming a quota slot, so repeated
        # identical requests don't exhaust the limit.
        cached = self._cache.get(
            config.url, config.preset, config.custom_overrides
        )
        if cached:
            # Peek at rate-limit state for response payload only — do NOT
            # call record(), since no audit work was performed.
            _, remaining, reset = self._rate_limiter.is_allowed(client_id)
            audit_id = self._cache._key(
                config.url, config.preset, config.custom_overrides
            )
            _log.info("audit complete (cache hit): id=%s score=%s",
                      audit_id, cached.overall_score)
            return StructuredOk(
                data=self._serialize_result(cached, remaining, reset)
            )

        allowed, remaining, reset = self._rate_limiter.is_allowed(client_id)
        if not allowed:
            _log.error("audit failed: code=RATE_LIMIT_EXCEEDED")
            return StructuredError(
                code="RATE_LIMIT_EXCEEDED",
                message=(
                    f"Rate limit exceeded. Retry after {reset}"
                ),
            )

        self._rate_limiter.record(client_id)

        context, error = await self._build_context_or_error(config.url)
        if error:
            _log.error("audit failed: code=%s", error.code)
            return error

        checks = await self._run_essential_checks(context, scope)
        agent_summary = await self._safe_agent_readiness_summary(
            context,
            scope.include_protocols,
            scope.include_account_auth,
            scope.include_ecommerce,
        )

        result = build_result(config.url, checks, agent_summary, scope)
        self._cache.put(
            config.url, config.preset, result, config.custom_overrides
        )

        audit_id = self._cache._key(
            config.url, config.preset, config.custom_overrides
        )
        _log.info("audit complete: id=%s score=%s", audit_id, result.overall_score)
        return StructuredOk(
            data=self._serialize_result(result, remaining, reset)
        )

    def get_audit_by_id(self, audit_id: str) -> AuditResultType:
        """Retrieve a cached audit by its ID."""
        cached = self._cache.get_by_audit_id(audit_id)
        if cached:
            return StructuredOk(data=self._serialize_result(cached, 0, 0))
        return StructuredError(
            code="AUDIT_NOT_FOUND",
            message=f"No audit found with ID: {audit_id}",
        )

    def list_checks(self) -> AuditResultType:
        """List all available Essentials checks with metadata."""
        checks_data = []
        for group in ESSENTIALS_CHECK_GROUPS:
            applicability = self._classify_applicability(group.check_name)
            scope_meta = self._scope_metadata(group.check_name)
            checks_data.append({
                "check_name": group.check_name,
                "pillar": group.pillar,
                "label": group.label,
                "max_score": group.max_score,
                "applicability": applicability,
                "available_in": "Essentials",
                "scope_metadata": scope_meta,
            })

        return StructuredOk(
            data={
                "checks": checks_data,
                "total_count": 13,
                "denominator": 56,
                "pillar_caps": {"off_site": 30, "scrapability": 40, "seo": 30},
            }
        )

    def _classify_applicability(self, check_name: str) -> str:
        """Classify check by applicability category."""
        universal = {
            "social", "wikipedia", "robots_txt", "bot_access",
            "html_structure", "schema_ld", "llms_txt", "ssr",
            "pagespeed", "canonical", "indexing",
        }
        contextual = {"search_discovery"}
        protocol = {"machine_surfaces"}

        if check_name in universal:
            return "universal"
        if check_name in contextual:
            return "contextual"
        if check_name in protocol:
            return "protocol/API-scoped"
        return "contextual"

    def _scope_metadata(self, check_name: str) -> dict:
        """Return scope-affecting metadata for check."""
        return {
            "protocols_affected": check_name == "machine_surfaces",
            "account_auth_affected": check_name == "machine_surfaces",
            "ecommerce_affected": check_name == "machine_surfaces",
        }

    def explain_check(self, check_name: str) -> AuditResultType:
        """Explain a specific check with full rubric coverage."""
        if check_name not in self._ESSENTIAL_GROUPS_BY_NAME:
            return StructuredError(
                code="CHECK_NOT_FOUND",
                message=f"Unknown check: {check_name}",
            )

        group = self._ESSENTIAL_GROUPS_BY_NAME[check_name]
        explanation = self._build_explanation(check_name, group)

        return StructuredOk(data=explanation)

    def _build_explanation(
        self, check_name: str, group: EssentialsCheckGroup
    ) -> dict:
        """Build comprehensive explanation for a check."""
        explanations: dict[str, dict] = {
            "robots_txt": {
                "sub_signals": [
                    "AI crawler directives (GPTBot, ClaudeBot, PerplexityBot)",
                    "Training/search/user-triggered splits",
                    "Sitemap references",
                    "Crawl-delay hints",
                    "Content-Signal headers",
                ],
                "scoring_rubric": "6 points: coverage across major AI agents",
                "finding_examples": [
                    "robots.txt mentions GPTBot but omits ClaudeBot",
                    "No sitemap reference found",
                ],
                "fix_examples": [
                    "Add directive: User-agent: ClaudeBot\\nDisallow:",
                    "Add Sitemap directive pointing to sitemap.xml",
                ],
            },
            "bot_access": {
                "sub_signals": [
                    "Browser vs bot fetch comparison",
                    "Final URL differences",
                    "Status code parity",
                    "Body size differences",
                ],
                "scoring_rubric": "6 points: accessible vs blocked bot fetches",
                "finding_examples": [
                    "Bot fetch returned 403 while browser fetch returned 200",
                    "Canonical mismatch between bot and browser",
                ],
                "fix_examples": [
                    "Ensure robots.txt allows GPTBot and ClaudeBot",
                    "Review CDN/WAF bot blocking rules",
                ],
            },
            "llms_txt": {
                "sub_signals": [
                    "llms.txt file presence",
                    "Markdown/text accessibility",
                    "Content-Type negotiation",
                    "Vary: Accept header handling",
                ],
                "scoring_rubric": "5 points: discoverable LLM text access",
                "finding_examples": [
                    "llms.txt not found at domain root",
                    "Content-Type is text/plain but no Vary header",
                ],
                "fix_examples": [
                    "Create /llms.txt with site overview and links",
                    "Add Vary: Accept to text/markdown responses",
                ],
            },
            "machine_surfaces": {
                "sub_signals": [
                    "MCP server card",
                    "A2A agent card",
                    "API catalog discovery",
                    "OAuth/OIDC endpoints",
                    "ARD catalog",
                ],
                "scoring_rubric": "3 points: protocol discovery metadata",
                "finding_examples": [
                    "No MCP server discovery found",
                    "Missing API catalog link header",
                ],
                "fix_examples": [
                    "Add /.well-known/mcp-server descriptor",
                    "Include Link: </api-catalog>; rel=api-catalog",
                ],
            },
            "html_structure": {
                "sub_signals": [
                    "Heading hierarchy (H1 count and H2/H3 order)",
                    "Semantic landmark presence",
                    "Title and meta description coverage",
                    "Form action and labelled fields",
                    "Image alt coverage",
                ],
                "scoring_rubric": "4 points: semantic HTML and form hygiene",
                "finding_examples": [
                    "Multiple H1 tags detected on homepage",
                    "Meta description missing or too short",
                ],
                "fix_examples": [
                    "Keep exactly one H1 per page and order H2/H3 logically",
                    "Add a concise meta description between 60 and 160 characters",
                ],
            },
            "schema_ld": {
                "sub_signals": [
                    "JSON-LD discovery and parse validity",
                    "Organization/LocalBusiness/Product presence",
                    "Required vs recommended field coverage",
                    "Schema vs visible-content coherence",
                    "Commerce-scoped Offer and aggregateRating fields",
                ],
                "scoring_rubric": "5 points: structured-data coverage and coherence",
                "finding_examples": [
                    "Organization schema missing name/logo/sameAs",
                    "Product schema lacks aggregateRating or Offer price",
                ],
                "fix_examples": [
                    "Add Organization JSON-LD with name, url, logo, and sameAs",
                    "Include Offer with priceCurrency, price, and availability on Product pages",
                ],
            },
            "ssr": {
                "sub_signals": [
                    "Raw visible word count",
                    "JavaScript app-shell hydration markers",
                    "Main-content word count vs boilerplate ratio",
                    "HTML-to-text extraction-readiness ratio",
                    "Block/empty, thin, readable, probable-JS-shell classification",
                ],
                "scoring_rubric": "4 points: server-rendered content extraction readiness",
                "finding_examples": [
                    "Page returns 200 OK but contains almost no visible text before JS executes",
                    "Vite dev-server markers present and word count is below threshold",
                ],
                "fix_examples": [
                    "Render primary content server-side so crawlers see it without JavaScript",
                    "Move large script/style payloads below meaningful content and reduce chrome ratio",
                ],
            },
            "pagespeed": {
                "sub_signals": [
                    "Homepage response time",
                    "Cache headers (Cache-Control, validator)",
                    "Render-blocking stylesheets",
                    "Unsized images and synchronous head scripts",
                    "Mobile viewport tag presence and responsive HTML hints",
                ],
                "scoring_rubric": "3 points: homepage performance for crawlers and humans",
                "finding_examples": [
                    "No cache-control header on the homepage response",
                    "Width/height attributes missing on most images",
                ],
                "fix_examples": [
                    "Add Cache-Control: public, max-age=... to homepage responses",
                    "Specify width and height on every image to prevent layout shift",
                ],
            },
            "canonical": {
                "sub_signals": [
                    "HTTP-to-HTTPS redirect behaviour",
                    "Canonical link tag presence",
                    "Canonical self-reference against final URL",
                    "www vs non-www alternate-host duplicate surface",
                ],
                "scoring_rubric": "5 points: canonical URL integrity across hosts",
                "finding_examples": [
                    "Canonical points to a different host than the final URL",
                    "No canonical link tag found on the homepage",
                ],
                "fix_examples": [
                    "Add <link rel=\"canonical\"> matching the final URL after redirects",
                    "Pick www vs non-www as canonical and 301-redirect the other",
                ],
            },
            "indexing": {
                "sub_signals": [
                    "Meta robots and crawler-specific meta tags (GPTBot, ClaudeBot, DuckDuckBot)",
                    "X-Robots-Tag including crawler-scoped directives",
                    "Blocking noindex/none",
                    "Restrictive nofollow, noarchive, nosnippet, max-snippet",
                    "unavailable_after directives",
                ],
                "scoring_rubric": "5 points: crawler-specific indexing permissiveness",
                "finding_examples": [
                    "Meta robots contains noindex on the homepage",
                    "X-Robots-Tag: max-snippet:0 across the site",
                ],
                "fix_examples": [
                    "Remove noindex/none and restrictive directives on crawler-accessible pages",
                    "Replace site-wide max-snippet:0 with a narrower robots policy if snippets are wanted",
                ],
            },
            "search_discovery": {
                "sub_signals": [
                    "Googlebot/Bingbot robots access",
                    "Sitemap discovery and index sampling",
                    "Sampled-URL accessibility, indexability, and metadata",
                    "Deterministic page-owned blurb coherence across titles and meta",
                    "Trust surfaces (About, Contact, Privacy, Terms) and feed discovery",
                ],
                "scoring_rubric": "4 points: sitemap, sampling, blurb, and trust-page discovery",
                "finding_examples": [
                    "Sitemap exists but no sampled URL exposes indexable metadata",
                    "Page blurb coherence diverges between meta description and visible content",
                ],
                "fix_examples": [
                    "Reference /sitemap.xml from robots.txt and ensure sampled URLs render with HTTPS, status 200, and metadata",
                    "Align meta descriptions with on-page heading content for blurb coherence",
                ],
            },
            "social": {
                "sub_signals": [
                    "Tracked homepage links for Instagram, TikTok, Facebook, X, YouTube, etc.",
                    "Open Graph title/description/URL/image coverage",
                    "Twitter card title/description/image/card",
                    "Canonical URL, page title, and meta description",
                    "Profile-link/schema overlap and metadata consistency",
                ],
                "scoring_rubric": "2 points: social and meta-tag presence for agents",
                "finding_examples": [
                    "og:image is missing or returns a non-image response",
                    "Twitter card meta is absent while Open Graph is present",
                ],
                "fix_examples": [
                    "Add og:title, og:description, og:image, and og:url on every public page",
                    "Include twitter:card, twitter:title, twitter:description, twitter:image",
                ],
            },
            "wikipedia": {
                "sub_signals": [
                    "Brand candidates from og:site_name, schema names, and title",
                    "Wikidata SPARQL official-website match",
                    "Wikidata label search",
                    "Wikipedia search title match",
                    "Cached lookup reuse and refusal handling",
                ],
                "scoring_rubric": "4 points: external entity authority surface",
                "finding_examples": [
                    "Brand entity was not found in Wikidata",
                    "Wikidata official-website URL does not match the audited domain",
                ],
                "fix_examples": [
                    "Add or correct the Wikidata item's official website property",
                    "Use a stable brand label in title/schema so Wikidata lookup matches",
                ],
            },
        }

        default_explanation = {
            "sub_signals": ["Standard signal evaluation per rubric"],
            "scoring_rubric": f"{group.max_score} points",
            "finding_examples": [f"Common {check_name} issues"],
            "fix_examples": [f"Standard {check_name} remediation"],
        }

        exp = explanations.get(check_name, default_explanation)

        return {
            "check_name": check_name,
            "pillar": group.pillar,
            "label": group.label,
            "max_score": group.max_score,
            **exp,
            "applicability": self._classify_applicability(check_name),
        }

    def _serialize_result(
        self, result: AuditResult, rate_remaining: int, rate_reset: int
    ) -> dict:
        """Serialize AuditResult for MCP tool output."""
        preset = result.scope.preset_applied
        overrides_applied = result.scope.overrides_applied or None
        return {
            "audit_id": self._cache._key(
                result.url, preset, overrides_applied
            ),
            "url": result.url,
            "preset": preset,
            "status": "completed",
            "api_version": result.api_version,
            "overall_score": result.overall_score,
            "pillars": result.pillar_scores.model_dump(),
            "pillars_max": result.pillar_max.model_dump(),
            "checks_count": len(result.checks),
            "rate_limit": {
                # use the configured RateLimiter
                # attribute (``_max_requests``) instead of hardcoding ``3``.
                # Using ``self._rate_limiter.limit`` would crash because
                # ``RateLimiter`` exposes its capacity as ``_max_requests``
                # (see audit_service.py:117).
                "limit": self._rate_limiter._max_requests,
                "remaining": rate_remaining,
                "reset_at": rate_reset,
            },
            "agent_readiness": {
                "label": result.agent_readiness.label,
                "score": result.agent_readiness.score,
                "earned": result.agent_readiness.earned,
                "max": result.agent_readiness.max,
            },
            "caveat": result.agent_readiness.caveat,
        }


# Global service instance for MCP tools
_default_service: AuditService | None = None


def get_audit_service() -> AuditService:
    """Get or create the global audit service instance."""
    global _default_service
    if _default_service is None:
        _default_service = AuditService(rate_limit=3, rate_window=60)
    return _default_service
