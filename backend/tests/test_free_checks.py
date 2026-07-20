import asyncio
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent_readiness import build_agent_readiness_summary
from app.audit_context import AuditContext
from app.benchmarks import build_agent_benchmark_comparison, build_benchmark_comparison
from app.crawler_registry import (
    CRAWLERS,
    bot_user_agents,
    crawler_directive_sources,
    crawler_groups,
    crawler_registry_caveat,
    fetch_probe_names,
    robots_scoring_crawlers,
)
from app.checks.bot_access import check_bot_access
from app.checks.locked import locked_checks
from app.checks.html_structure import _analyse as analyse_html_structure, check_html_structure
from app.checks.indexing import _has_zero_snippet_limit, check_indexing
from app.checks.llms_txt import _analyse_sitemap, analyse_markdown_response, check_llms_txt
from app.checks.machine_surfaces import check_machine_surfaces
from app.checks.pagespeed import _mobile_viewport_signal
from app.checks.robots import (
    _classify_bot_access,
    check_robots,
    content_signal_tokens,
    explicit_ai_bot_count,
    response_content_signal_tokens,
    robots_quality_signals,
)
from app.checks.search_discovery import (
    FeedFreshnessResult,
    HreflangValidationResult,
    SamplePageMetadataResult,
    _freshness_surface,
    _parse_feed_response,
    _sample_pages_have_discovery_metadata,
    _trust_surface_discovery,
    _validate_hreflang,
    check_search_discovery,
)
from app.checks.schema_ld import _extract_schemas, _score_schema, check_schema_ld
from app.checks.sitemap_analysis import SitemapEntry, SitemapSampleResult, parse_sitemap
from app.checks.social import check_social
from app.checks.ssr import _analyse_rendering, _extraction_efficiency, check_ssr
from app.entity_cache import get_cached_entity_lookup, set_cached_entity_lookup
from app.fetching import BROWSER_USER_AGENT, FetchResult
from app.models import AgentReadinessSummary, CheckResult
from app.rubric import ESSENTIALS_CHECKED_MAX, ESSENTIALS_CHECK_GROUP_COUNT, ESSENTIALS_CHECK_GROUPS
from app.scoring import build_result
from fixtures import (
    BASIC_SITEMAP,
    EXAMPLE_URL,
    ROBOTS_WITH_SITEMAP,
    SHOP_URL,
    make_audit_context,
    make_fetch_result,
)


class SchemaLdTests(unittest.TestCase):
    def _context(self, homepage_html: str) -> AuditContext:
        return make_audit_context(
            base_url=SHOP_URL,
            homepage_html=homepage_html,
            homepage_final_url=f"{SHOP_URL}/",
        )

    def test_extracts_schema_graph_and_scores_best_entity(self) -> None:
        html = """
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@graph": [
            {"@type": "WebSite", "name": "Shop", "url": "https://shop.example"},
            {
              "@type": "Product",
              "name": "Trail Mug",
              "description": "Insulated mug",
              "image": "https://shop.example/mug.jpg",
              "brand": {"@type": "Brand", "name": "Shop"},
              "sku": "MUG-24",
              "gtin13": "1234567890123",
              "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.8"},
              "offers": {
                "@type": "Offer",
                "price": "24",
                "priceCurrency": "USD",
                "availability": "https://schema.org/InStock",
                "priceValidUntil": "2999-12-31",
                "shippingDetails": {
                  "@type": "OfferShippingDetails",
                  "shippingDestination": {"@type": "DefinedRegion", "addressCountry": "US"}
                },
                "hasMerchantReturnPolicy": {
                  "@type": "MerchantReturnPolicy",
                  "applicableCountry": "US",
                  "returnPolicyCategory": "https://schema.org/MerchantReturnFiniteReturnWindow"
                }
              }
            }
          ]
        }
        </script>
        """
        schemas, invalid_count = _extract_schemas(BeautifulSoup(html, "lxml"))
        scored = [_score_schema(schema, include_ecommerce=True) for schema in schemas]

        self.assertEqual(invalid_count, 0)
        self.assertEqual(len(schemas), 2)
        self.assertIn((5, "Product", []), scored)

    def test_ecommerce_product_reports_expanded_missing_fields(self) -> None:
        score, schema_type, missing = _score_schema(
            {
                "@type": "Product",
                "name": "Trail Mug",
                "description": "Insulated mug",
                "offers": {
                    "@type": "Offer",
                    "price": "24",
                    "priceCurrency": "USD",
                    "availability": "https://schema.org/InStock",
                },
            },
            include_ecommerce=True,
        )

        self.assertEqual(score, 3)
        self.assertEqual(schema_type, "Product")
        self.assertEqual(
            missing,
            [
                "recommended image",
                "recommended brand",
                "recommended sku",
                "recommended gtin",
                "recommended aggregateRating or review",
                "recommended offers.priceValidUntil",
                "recommended offers.shippingDetails",
                "recommended offers.hasMerchantReturnPolicy",
            ],
        )

    def test_ecommerce_offer_reports_price_shipping_and_return_details(self) -> None:
        score, schema_type, missing = _score_schema(
            {
                "@type": "Offer",
                "price": "24",
                "priceCurrency": "USD",
                "availability": "https://schema.org/InStock",
            },
            include_ecommerce=True,
        )

        self.assertEqual(score, 3)
        self.assertEqual(schema_type, "Offer")
        self.assertEqual(
            missing,
            [
                "recommended priceValidUntil",
                "recommended shippingDetails",
                "recommended hasMerchantReturnPolicy",
            ],
        )

    def test_commerce_scope_surfaces_product_schema_over_complete_website(self) -> None:
        html = """
        <html><head>
          <script type="application/ld+json">
          [
            {
              "@type": "WebSite",
              "name": "Shop",
              "url": "https://shop.example/",
              "description": "A complete store website entity."
            },
            {
              "@type": "Product",
              "name": "Trail Mug",
              "description": "Insulated mug",
              "offers": {
                "@type": "Offer",
                "price": "24",
                "priceCurrency": "USD",
                "availability": "https://schema.org/InStock"
              }
            }
          ]
          </script>
        </head><body></body></html>
        """

        result = asyncio.run(check_schema_ld(self._context(html), include_ecommerce=True))

        self.assertEqual(result.state, "partial")
        self.assertIn("Found Product JSON-LD schema", result.finding)
        self.assertIn("recommended sku", result.finding)

    def test_non_commerce_scope_does_not_penalize_commerce_recommended_fields(self) -> None:
        html = """
        <html><head>
          <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": "Trail Mug",
            "description": "Insulated mug",
            "image": "https://shop.example/mug.jpg",
            "brand": {"@type": "Brand", "name": "Shop"},
            "offers": {
              "@type": "Offer",
              "price": "24",
              "priceCurrency": "USD",
              "availability": "https://schema.org/InStock"
            }
          }
          </script>
        </head><body></body></html>
        """

        result = asyncio.run(check_schema_ld(self._context(html), include_ecommerce=False))

        self.assertEqual(result.state, "pass")
        self.assertEqual(result.score, 5)
        self.assertNotIn("recommended sku", result.finding)
        self.assertNotIn("shipping", result.fix.lower())

    def test_speakable_detected_in_finding(self) -> None:
        """QA4-04: Speakable markup is a tracked-only signal — finding annotates it but score is unchanged.

        The same Product JSON-LD is run twice: once without speakable (baseline)
        and once with a SpeakableSpecification @type. The score, state, fix, and
        effort must be identical; only the finding text gains the
        ``Speakable markup detected`` note.
        """
        baseline_html = """
        <html><head>
          <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": "Trail Mug",
            "description": "Insulated mug",
            "image": "https://shop.example/mug.jpg",
            "brand": {"@type": "Brand", "name": "Shop"},
            "offers": {
              "@type": "Offer",
              "price": "24",
              "priceCurrency": "USD",
              "availability": "https://schema.org/InStock"
            }
          }
          </script>
        </head><body></body></html>
        """
        speakable_html = """
        <html><head>
          <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": "Trail Mug",
            "description": "Insulated mug",
            "image": "https://shop.example/mug.jpg",
            "brand": {"@type": "Brand", "name": "Shop"},
            "offers": {
              "@type": "Offer",
              "price": "24",
              "priceCurrency": "USD",
              "availability": "https://schema.org/InStock"
            },
            "speakable": {
              "@type": "SpeakableSpecification",
              "xpath": ["/html/head/title", "/html/body/p[1]"]
            }
          }
          </script>
        </head><body></body></html>
        """

        baseline = asyncio.run(
            check_schema_ld(self._context(baseline_html), include_ecommerce=False)
        )
        annotated = asyncio.run(
            check_schema_ld(self._context(speakable_html), include_ecommerce=False)
        )

        # Finding carries the tracked-only speakable note.
        self.assertIn("Speakable markup detected", annotated.finding)
        self.assertNotIn("Speakable markup detected", baseline.finding)
        # Score / state / fix / effort are all unchanged — speakable is read-only.
        self.assertEqual(annotated.score, baseline.score)
        self.assertEqual(annotated.state, baseline.state)
        self.assertEqual(annotated.fix, baseline.fix)
        self.assertEqual(annotated.effort, baseline.effort)


class SocialMetadataTests(unittest.IsolatedAsyncioTestCase):
    def _context(self, homepage_html: str) -> AuditContext:
        return make_audit_context(
            homepage_html=homepage_html,
            homepage_final_url=f"{EXAMPLE_URL}/",
        )

    async def test_passes_when_social_cards_and_schema_entity_align(self) -> None:
        html = """
        <html>
          <head>
            <title>Acme AI - Agent-ready audits</title>
            <meta name="description" content="Acme AI helps teams prepare websites for AI agents.">
            <link rel="canonical" href="https://example.com/">
            <meta property="og:title" content="Acme AI">
            <meta property="og:description" content="Acme AI helps teams prepare websites for AI agents.">
            <meta property="og:url" content="https://example.com/">
            <meta property="og:site_name" content="Acme AI">
            <meta property="og:image" content="https://example.com/og.png">
            <meta name="twitter:card" content="summary_large_image">
            <meta name="twitter:title" content="Acme AI">
            <meta name="twitter:description" content="Acme AI helps teams prepare websites for AI agents.">
            <meta name="twitter:image" content="https://example.com/og.png">
            <script type="application/ld+json">
            {
              "@context": "https://schema.org",
              "@type": "Organization",
              "name": "Acme AI",
              "url": "https://example.com/",
              "logo": "https://example.com/logo.png",
              "sameAs": [
                "https://www.linkedin.com/company/acme-ai",
                "https://x.com/acmeai",
                "https://www.youtube.com/@acmeai"
              ]
            }
            </script>
          </head>
          <body>
            <a href="https://www.linkedin.com/company/acme-ai">LinkedIn</a>
            <a href="https://x.com/acmeai">X</a>
            <a href="https://www.youtube.com/@acmeai">YouTube</a>
          </body>
        </html>
        """

        result = await check_social(self._context(html))

        self.assertEqual(result.label, "Social & Entity Metadata")
        self.assertEqual(result.state, "pass")
        self.assertEqual(result.score, 2)
        self.assertIn("Open Graph, Twitter card, canonical", result.finding)

    async def test_reports_open_graph_schema_and_same_as_mismatches(self) -> None:
        html = """
        <html>
          <head>
            <title>Acme AI</title>
            <meta name="description" content="Acme AI helps teams prepare websites for AI agents.">
            <link rel="canonical" href="https://example.com/">
            <meta property="og:title" content="Other Brand">
            <meta property="og:description" content="A different company profile.">
            <meta property="og:url" content="https://other.example/">
            <meta property="og:image" content="https://example.com/og.png">
            <script type="application/ld+json">
            {
              "@context": "https://schema.org",
              "@type": "Organization",
              "name": "Different Co",
              "url": "https://example.com/",
              "logo": "https://example.com/logo.png",
              "sameAs": ["https://www.linkedin.com/company/different-co"]
            }
            </script>
          </head>
          <body><a href="https://x.com/acmeai">X</a></body>
        </html>
        """

        result = await check_social(self._context(html))

        self.assertEqual(result.state, "partial")
        self.assertEqual(result.score, 1)
        self.assertIn("og:title does not align with page title", result.finding)
        self.assertIn("og:url does not match canonical URL", result.finding)
        self.assertIn("Detected tracked profile signal(s): X/Twitter, LinkedIn", result.finding)


class HtmlStructureTests(unittest.TestCase):
    def _context(self, homepage_html: str) -> AuditContext:
        return make_audit_context(homepage_html=homepage_html)

    def test_accepts_clear_static_form_affordances(self) -> None:
        soup = BeautifulSoup(
            """
            <html>
              <head>
                <title>Contact Acme AI</title>
                <meta name="description" content="Contact Acme AI for agent-ready website audits.">
              </head>
              <body>
                <header><nav><a href="/contact">Contact</a></nav></header>
                <main>
                  <article>
                    <h1>Contact Acme AI</h1>
                    <h2>Tell us about your site</h2>
                    <form action="/contact" method="post">
                      <label for="full-name">Name</label>
                      <input id="full-name" name="full_name" autocomplete="name">
                      <label for="email">Email</label>
                      <input id="email" name="email" type="email" autocomplete="email">
                      <label for="message">Message</label>
                      <textarea id="message" name="message"></textarea>
                      <button type="submit">Send request</button>
                    </form>
                  </article>
                </main>
                <footer>Acme AI</footer>
              </body>
            </html>
            """,
            "lxml",
        )

        score, issues = analyse_html_structure(soup)

        self.assertEqual(score, 4)
        self.assertEqual(issues, [])

    def test_reports_javascript_only_forms_and_weak_field_affordances(self) -> None:
        soup = BeautifulSoup(
            """
            <html>
              <head>
                <title>Contact Acme AI</title>
                <meta name="description" content="Contact Acme AI for agent-ready website audits.">
              </head>
              <body>
                <header><nav><a href="/contact">Contact</a></nav></header>
                <main>
                  <section>
                    <h1>Contact Acme AI</h1>
                    <h2>Tell us about your site</h2>
                    <form action="javascript:void(0)" onsubmit="sendLead(event)">
                      <label for="email">Email</label>
                      <input id="email" name="field" type="email">
                      <label for="phone">Phone</label>
                      <input id="phone" type="tel">
                      <input type="submit">
                    </form>
                  </section>
                </main>
                <footer>Acme AI</footer>
              </body>
            </html>
            """,
            "lxml",
        )

        score, issues = analyse_html_structure(soup)

        self.assertEqual(score, 1)
        self.assertIn("1 form(s) use JavaScript-only actions", issues)
        self.assertIn("1 form(s) lack explicit methods", issues)
        self.assertIn("2 form field(s) lack useful name attributes", issues)
        self.assertIn("2 form field(s) lack autocomplete hints", issues)
        self.assertIn("1 form(s) lack named submit controls", issues)
        self.assertIn("no form exposes an explicit non-JS action/method fallback", issues)

    def test_report_surfaces_form_affordance_issues(self) -> None:
        html = """
        <html>
          <head>
            <title>Contact Acme AI</title>
            <meta name="description" content="Contact Acme AI for agent-ready website audits.">
          </head>
          <body>
            <header><nav><a href="/contact">Contact</a></nav></header>
            <main>
              <section>
                <h1>Contact Acme AI</h1>
                <h2>Tell us about your site</h2>
                <form action="javascript:void(0)">
                  <label for="email">Email</label>
                  <input id="email" name="field" type="email">
                  <input type="submit">
                </form>
              </section>
            </main>
            <footer>Acme AI</footer>
          </body>
        </html>
        """

        result = asyncio.run(check_html_structure(self._context(html)))

        self.assertEqual(result.state, "fail")
        self.assertIn("form(s) use JavaScript-only actions", result.finding)
        self.assertIn("form field(s) lack autocomplete hints", result.finding)


class RenderingTests(unittest.TestCase):
    def _context(self, homepage_html: str) -> AuditContext:
        return make_audit_context(homepage_html=homepage_html)

    def test_thin_static_page_is_not_js_shell(self) -> None:
        result, words = _analyse_rendering("<html><body><h1>Example Domain</h1><p>Short text.</p></body></html>")

        self.assertEqual(result, "thin")
        self.assertGreater(words, 0)

    def test_app_shell_is_detected_when_html_is_thin(self) -> None:
        html = """
        <html><body><div id="__next"></div>
        <script src="/a.js"></script><script src="/b.js"></script>
        <script src="/c.js"></script><script src="/d.js"></script>
        </body></html>
        """

        result, _ = _analyse_rendering(html)

        self.assertEqual(result, "probable_js_shell")

    def test_extraction_efficiency_accepts_clear_main_content(self) -> None:
        main_copy = " ".join(["Useful product and company detail"] * 35)
        nav_copy = "Home Products About Contact"
        html = f"""
        <html>
          <body>
            <header><nav>{nav_copy}</nav></header>
            <main><h1>Trail Mug</h1><p>{main_copy}</p></main>
          </body>
        </html>
        """

        signal = _extraction_efficiency(html)

        self.assertEqual(signal.issues, [])
        self.assertGreaterEqual(signal.main_content_words, 120)
        self.assertLess(signal.navigation_ratio, 0.1)

    def test_extraction_efficiency_flags_navigation_heavy_payloads(self) -> None:
        nav_copy = " ".join(["Menu link"] * 80)
        html = f"""
        <html>
          <body>
            <header><nav>{nav_copy}</nav></header>
            <main><p>Short page copy.</p></main>
            <script>var payload = "{'x' * 50000}";</script>
          </body>
        </html>
        """

        signal = _extraction_efficiency(html)

        self.assertIn("main-content area has only 3 words", signal.issues)
        self.assertTrue(any(issue.startswith("navigation-heavy page") for issue in signal.issues))
        self.assertTrue(any(issue.startswith("script/style markup is heavy") for issue in signal.issues))
        self.assertTrue(any(issue.startswith("high HTML-to-text ratio") for issue in signal.issues))

    def test_readable_page_with_extraction_issues_is_partial(self) -> None:
        nav_copy = " ".join(["Menu link"] * 80)
        html = f"""
        <html>
          <body>
            <header><nav>{nav_copy}</nav></header>
            <main><p>Short page copy.</p></main>
            <script>var payload = "{'x' * 50000}";</script>
          </body>
        </html>
        """

        result = asyncio.run(check_ssr(self._context(html)))

        self.assertEqual(result.state, "partial")
        self.assertEqual(result.score, 2)
        self.assertIn("Extraction proxy flags", result.finding)
        self.assertIn("main-content words", result.finding)


class SitemapTests(unittest.TestCase):
    def test_parses_namespaced_urlset(self) -> None:
        sitemap = """
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://example.com/</loc><lastmod>2026-06-01</lastmod></url>
        </urlset>
        """

        self.assertEqual(_analyse_sitemap(sitemap), (True, 1, True, False))

    def test_parses_sitemap_index(self) -> None:
        sitemap = """
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <sitemap><loc>https://example.com/posts.xml</loc></sitemap>
        </sitemapindex>
        """

        self.assertEqual(_analyse_sitemap(sitemap), (True, 1, False, True))

    def test_tracks_invalid_and_future_lastmod_values(self) -> None:
        sitemap = """
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://example.com/a</loc><lastmod>not-a-date</lastmod></url>
          <url><loc>https://example.com/b</loc><lastmod>2999-01-01</lastmod></url>
          <url><loc>https://example.com/c</loc></url>
        </urlset>
        """

        parsed = parse_sitemap(sitemap)

        self.assertTrue(parsed.is_valid)
        self.assertEqual(parsed.loc_count, 3)
        self.assertEqual(parsed.invalid_lastmod_count, 1)
        self.assertEqual(parsed.future_lastmod_count, 1)
        self.assertEqual(parsed.missing_lastmod_count, 1)


class FeedFreshnessTests(unittest.IsolatedAsyncioTestCase):
    def _context(self, homepage_html: str) -> AuditContext:
        return make_audit_context(
            homepage_html=homepage_html,
            homepage_final_url=f"{EXAMPLE_URL}/",
        )

    def test_parses_rss_and_atom_feed_dates(self) -> None:
        today = date.today()
        latest = today - timedelta(days=5)
        older = today - timedelta(days=30)
        rss = f"""
        <rss version="2.0"><channel>
          <item><title>Latest</title><pubDate>{latest.strftime('%a, %d %b %Y 12:00:00 GMT')}</pubDate></item>
          <item><title>Older</title><pubDate>{older.isoformat()}</pubDate></item>
        </channel></rss>
        """
        atom = f"""
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry><title>Latest</title><updated>{latest.isoformat()}T08:00:00Z</updated></entry>
        </feed>
        """

        rss_result = _parse_feed_response(
            FetchResult(
                "https://example.com/feed.xml",
                "https://example.com/feed.xml",
                200,
                {"content-type": "application/rss+xml"},
                rss,
                1,
                [],
            ),
            today=today,
        )
        atom_result = _parse_feed_response(
            FetchResult(
                "https://example.com/atom.xml",
                "https://example.com/atom.xml",
                200,
                {"content-type": "application/atom+xml"},
                atom,
                1,
                [],
            ),
            today=today,
        )

        self.assertEqual(rss_result.feed_type, "RSS")
        self.assertEqual(rss_result.item_count, 2)
        self.assertEqual(rss_result.latest_date, latest)
        self.assertEqual(rss_result.invalid_date_count, 0)
        self.assertEqual(atom_result.feed_type, "Atom")
        self.assertEqual(atom_result.item_count, 1)
        self.assertEqual(atom_result.latest_date, latest)

    async def test_freshness_surface_reports_linked_feed_details(self) -> None:
        today = date.today()
        latest = today - timedelta(days=3)
        feed = f"""
        <rss version="2.0"><channel>
          <item><title>Latest</title><pubDate>{latest.isoformat()}</pubDate></item>
          <item><title>Older</title><pubDate>{(today - timedelta(days=15)).isoformat()}</pubDate></item>
        </channel></rss>
        """
        homepage = '<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head></html>'

        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            if url == "https://example.com/feed.xml":
                return FetchResult(url, url, 200, {"content-type": "application/rss+xml"}, feed, 1, [])
            return FetchResult(url, url, 404, {"content-type": "text/plain"}, "not found", 1, [])

        with patch("app.checks.search_discovery.fetch_url", new=fake_fetch):
            result = await _freshness_surface(self._context(homepage))

        self.assertTrue(result.available)
        self.assertIn(f"linked RSS feed has 2 item(s); latest item dated {latest.isoformat()}", result.positives)
        self.assertEqual(result.issues, [])
        self.assertEqual(result.caveats, [])

    async def test_freshness_surface_reports_json_feed_stale_and_invalid_dates(self) -> None:
        today = date.today()
        stale = today - timedelta(days=365)
        feed = (
            '{"version":"https://jsonfeed.org/version/1.1",'
            '"items":['
            f'{{"title":"Old","date_published":"{stale.isoformat()}T08:00:00Z"}},'
            '{"title":"Broken","date_published":"not-a-date"}'
            "]}"
        )
        homepage = '<html><head><link rel="alternate" type="application/feed+json" href="/feed.json"></head></html>'

        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            if url == "https://example.com/feed.json":
                return FetchResult(url, url, 200, {"content-type": "application/feed+json"}, feed, 1, [])
            return FetchResult(url, url, 404, {"content-type": "text/plain"}, "not found", 1, [])

        with patch("app.checks.search_discovery.fetch_url", new=fake_fetch):
            result = await _freshness_surface(self._context(homepage))

        self.assertTrue(result.available)
        self.assertIn(f"linked JSON Feed has 2 item(s); latest item dated {stale.isoformat()}", result.positives)
        self.assertIn("1 feed item date(s) were invalid", result.caveats)
        self.assertIn(f"latest feed item is older than 180 days ({stale.isoformat()})", result.caveats)


class TrustSurfaceTests(unittest.IsolatedAsyncioTestCase):
    def _context(self, homepage_html: str) -> AuditContext:
        return make_audit_context(
            homepage_html=homepage_html,
            homepage_final_url=f"{EXAMPLE_URL}/",
            robots_text=ROBOTS_WITH_SITEMAP,
        )

    def test_discovers_homepage_and_sitemap_trust_surfaces(self) -> None:
        html = """
        <html><body>
          <nav>
            <a href="/about">About</a>
            <a href="/contact-us">Contact</a>
            <a href="/privacy-policy">Privacy</a>
            <a href="/terms-of-service">Terms</a>
            <a href="/help-center">Support</a>
          </nav>
        </body></html>
        """

        result = _trust_surface_discovery(
            self._context(html),
            [
                SitemapEntry(loc="https://example.com/return-policy", lastmod="2026-06-01"),
                SitemapEntry(loc="https://example.com/shipping", lastmod="2026-06-01"),
            ],
            include_ecommerce=True,
        )

        self.assertEqual(result.issues, [])
        self.assertEqual(result.caveats, [])
        self.assertIn("about", result.found)
        self.assertIn("returns", result.found)
        self.assertIn("core About, Contact, and Privacy surfaces are discoverable", result.positives)
        self.assertIn("commerce Returns and Shipping surfaces are discoverable", result.positives)

    def test_reports_missing_core_and_commerce_trust_surfaces(self) -> None:
        result = _trust_surface_discovery(
            self._context('<html><body><a href="/about-us">About us</a></body></html>'),
            [],
            include_ecommerce=True,
        )

        self.assertIn("missing core trust/entity pages: Contact, Privacy", result.issues)
        self.assertIn("commerce scope missing policy pages: Returns, Shipping", result.issues)
        self.assertIn("recommended trust/support pages were not discovered: Terms, Support", result.caveats)

    def test_non_commerce_scope_does_not_penalize_returns_or_shipping(self) -> None:
        result = _trust_surface_discovery(
            self._context('<html><body><a href="/about-us">About us</a></body></html>'),
            [],
            include_ecommerce=False,
        )

        self.assertIn("missing core trust/entity pages: Contact, Privacy", result.issues)
        self.assertNotIn("commerce scope missing policy pages: Returns, Shipping", result.issues)

    async def test_search_discovery_caps_score_when_trust_surfaces_are_missing(self) -> None:
        sample = SitemapSampleResult(
            entries=[SitemapEntry(loc="https://example.com/", lastmod="2026-06-01")],
            sitemap_count=1,
            is_index=False,
            has_robot_reference=True,
            invalid_lastmod_count=0,
            future_lastmod_count=0,
            missing_lastmod_count=0,
            same_host_count=1,
            non_https_count=0,
            duplicate_count=0,
            issues=[],
        )

        with (
            patch("app.checks.search_discovery.collect_sitemap_sample", new=AsyncMock(return_value=sample)),
            patch(
                "app.checks.search_discovery._freshness_surface",
                new=AsyncMock(
                    return_value=FeedFreshnessResult(
                        True,
                        ["dated content metadata on the homepage"],
                        [],
                        [],
                    )
                ),
            ),
            patch(
                "app.checks.search_discovery._sample_pages_have_discovery_metadata",
                new=AsyncMock(
                    return_value=SamplePageMetadataResult(
                        True,
                        ["sampled pages include titles"],
                        [],
                        [],
                    )
                ),
            ),
            patch(
                "app.checks.search_discovery._validate_hreflang",
                new=AsyncMock(return_value=HreflangValidationResult(False, [], [], [])),
            ),
        ):
            result = await check_search_discovery(
                self._context("<html><body><main>Example</main></body></html>"),
                include_ecommerce=True,
            )

        self.assertEqual(result.state, "partial")
        self.assertEqual(result.score, 3)
        self.assertIn("missing core trust/entity pages: About, Contact, Privacy", result.finding)
        self.assertIn("commerce scope missing policy pages: Returns, Shipping", result.finding)

    async def test_search_discovery_passes_when_included_free_signals_align(self) -> None:
        sample = SitemapSampleResult(
            entries=[SitemapEntry(loc="https://example.com/about", lastmod="2026-06-01")],
            sitemap_count=1,
            is_index=False,
            has_robot_reference=True,
            invalid_lastmod_count=0,
            future_lastmod_count=0,
            missing_lastmod_count=0,
            same_host_count=1,
            non_https_count=0,
            duplicate_count=0,
            issues=[],
        )
        html = """
        <html><body>
          <nav>
            <a href="/about">About</a>
            <a href="/contact">Contact</a>
            <a href="/privacy">Privacy</a>
            <a href="/terms">Terms</a>
            <a href="/support">Support</a>
          </nav>
          <main>Example</main>
        </body></html>
        """

        with (
            patch("app.checks.search_discovery.collect_sitemap_sample", new=AsyncMock(return_value=sample)),
            patch(
                "app.checks.search_discovery._freshness_surface",
                new=AsyncMock(
                    return_value=FeedFreshnessResult(
                        True,
                        ["linked RSS feed has 3 item(s); latest item dated 2026-07-01"],
                        [],
                        [],
                    )
                ),
            ),
            patch(
                "app.checks.search_discovery._sample_pages_have_discovery_metadata",
                new=AsyncMock(
                    return_value=SamplePageMetadataResult(
                        True,
                        [
                            "sampled pages include titles",
                            "sampled pages include same-site canonicals",
                            "sampled pages include parseable JSON-LD",
                        ],
                        [],
                        [],
                    )
                ),
            ),
            patch(
                "app.checks.search_discovery._validate_hreflang",
                new=AsyncMock(return_value=HreflangValidationResult(False, [], [], [])),
            ),
        ):
            result = await check_search_discovery(self._context(html))

        self.assertEqual(result.state, "pass")
        self.assertEqual(result.score, 4)
        self.assertIn("Included search discovery hints are strong", result.finding)
        self.assertIn("sampled pages include titles", result.finding)
        self.assertIn("core About, Contact, and Privacy surfaces are discoverable", result.finding)


class IndexingTests(unittest.IsolatedAsyncioTestCase):
    def _context(self, homepage_html: str, headers: dict[str, str] | None = None) -> AuditContext:
        return make_audit_context(homepage_html=homepage_html, homepage_headers=headers or None)

    def test_detects_zero_snippet_limits(self) -> None:
        self.assertTrue(_has_zero_snippet_limit("index, max-snippet: 0"))
        self.assertTrue(_has_zero_snippet_limit("max-image-preview:none"))
        self.assertTrue(_has_zero_snippet_limit("bingbot: max-video-preview: 0"))
        self.assertFalse(_has_zero_snippet_limit("max-snippet:-1, max-image-preview:large"))

    async def test_reports_data_nosnippet_as_snippet_restriction(self) -> None:
        result = await check_indexing(
            self._context("<html><body><p data-nosnippet>Private snippet text</p></body></html>")
        )

        self.assertEqual(result.state, "partial")
        self.assertEqual(result.score, 2)
        self.assertIn("data-nosnippet", result.finding)
        self.assertIn("data-nosnippet", result.fix)

    async def test_reads_per_bot_x_robots_directives(self) -> None:
        result = await check_indexing(
            self._context(
                "<html><body>Example</body></html>",
                {"content-type": "text/html", "x-robots-tag": "googlebot: noindex, bingbot: max-snippet: 0"},
            )
        )

        self.assertEqual(result.state, "fail")
        self.assertEqual(result.score, 0)
        self.assertIn("X-Robots-Tag googlebot", result.finding)

    async def test_treats_standard_image_preview_as_limited(self) -> None:
        result = await check_indexing(
            self._context(
                '<html><head><meta name="robots" content="index, max-image-preview:standard"></head></html>'
            )
        )

        self.assertEqual(result.state, "partial")
        self.assertEqual(result.score, 4)
        self.assertIn("limits large image previews", result.finding)

    async def test_treats_large_image_preview_as_positive(self) -> None:
        result = await check_indexing(
            self._context(
                '<html><head><meta name="robots" content="index, max-image-preview:large"></head></html>'
            )
        )

        self.assertEqual(result.state, "pass")
        self.assertEqual(result.score, 5)
        self.assertIn("Large image previews are allowed", result.finding)


class MobileViewportTests(unittest.TestCase):
    def test_accepts_device_width_viewport_and_responsive_hints(self) -> None:
        soup = BeautifulSoup(
            """
            <html>
              <head>
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>@media (max-width: 700px) { main { display: block; } }</style>
              </head>
              <body><picture><source srcset="/hero.webp"><img src="/hero.jpg"></picture></body>
            </html>
            """,
            "lxml",
        )

        result = _mobile_viewport_signal(soup)

        self.assertEqual(result.score, 4)
        self.assertEqual(result.issues, [])
        self.assertIn("mobile viewport sets width=device-width without zoom restrictions", result.positives)
        self.assertIn("sampled HTML exposes responsive layout or image hints", result.positives)

    def test_reports_missing_mobile_viewport(self) -> None:
        soup = BeautifulSoup("<html><head><title>Example</title></head><body>Example</body></html>", "lxml")

        result = _mobile_viewport_signal(soup)

        self.assertEqual(result.score, 0)
        self.assertIn("missing mobile viewport meta tag", result.issues)

    def test_reports_zoom_restrictive_viewport(self) -> None:
        soup = BeautifulSoup(
            '<html><head><meta name="viewport" content="width=device-width, maximum-scale=1"></head></html>',
            "lxml",
        )

        result = _mobile_viewport_signal(soup)

        self.assertEqual(result.score, 2)
        self.assertIn("mobile viewport restricts pinch zoom", result.issues)


class MarkdownAccessTests(unittest.IsolatedAsyncioTestCase):
    def test_accepts_meaningful_negotiated_markdown(self) -> None:
        body = "# Example\n\n" + "Useful Markdown content for agents. " * 20 + "\n[Home](https://example.com)"
        response = FetchResult(
            requested_url="https://example.com",
            final_url="https://example.com",
            status_code=200,
            headers={
                "Content-Type": "text/markdown; charset=utf-8",
                "Vary": "Accept, Accept-Encoding",
                "Link": '<https://example.com/index.md>; rel="alternate"; type="text/markdown"',
            },
            text=body,
            elapsed_ms=1,
            redirect_chain=[],
        )

        result = analyse_markdown_response(response, "homepage Markdown negotiation", True)

        self.assertTrue(result.available)
        self.assertEqual(result.issues, [])
        self.assertIn("Vary: Accept present", result.detail or "")
        self.assertIn("Link header advertises Markdown/text alternate", result.hints)

    def test_rejects_html_body_for_markdown_access(self) -> None:
        response = FetchResult(
            requested_url="https://example.com",
            final_url="https://example.com",
            status_code=200,
            headers={"content-type": "text/html", "vary": "Accept"},
            text="<html><body><h1>Example</h1><p>This is a normal page.</p></body></html>",
            elapsed_ms=1,
            redirect_chain=[],
        )

        result = analyse_markdown_response(response, "homepage Markdown negotiation", True)

        self.assertFalse(result.available)
        self.assertIn("body is HTML, not Markdown/text", result.issues)
        self.assertIn("Content-Type is not text/markdown or text/plain", result.issues)

    async def test_report_includes_markdown_negotiation_caveat(self) -> None:
        context = make_audit_context(
            homepage_headers={},
            sitemap_text=(
                "<urlset><url><loc>https://example.com</loc>"
                "<lastmod>2026-06-01</lastmod></url></urlset>"
            ),
        )
        markdown_body = "# Example\n\n" + "Useful Markdown body for agents. " * 20

        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            if url == "https://example.com":
                return FetchResult(
                    requested_url=url,
                    final_url=url,
                    status_code=200,
                    headers={"content-type": "text/markdown"},
                    text=markdown_body,
                    elapsed_ms=1,
                    redirect_chain=[],
                )
            return FetchResult(
                requested_url=url,
                final_url=url,
                status_code=404,
                headers={"content-type": "text/plain"},
                text="not found",
                elapsed_ms=1,
                redirect_chain=[],
            )

        with patch("app.checks.llms_txt.fetch_url", new=fake_fetch):
            result = await check_llms_txt(context)

        self.assertEqual(result.state, "partial")
        self.assertIn("Vary: Accept is missing", result.finding)
        self.assertIn("Serve substantial Markdown", result.fix)


class SearchDiscoverySampleTests(unittest.IsolatedAsyncioTestCase):
    def _context(self) -> AuditContext:
        return make_audit_context()

    async def test_sampled_page_metadata_accepts_markdown_alternate(self) -> None:
        page_url = "https://example.com/products/widget"
        page_html = """
        <html>
          <head>
            <title>Widget</title>
            <link rel="canonical" href="https://example.com/products/widget">
            <meta name="robots" content="index,follow">
            <link rel="alternate" type="text/markdown" href="/products/widget.md">
            <script type="application/ld+json">
            {{"@context":"https://schema.org","@type":"Product","name":"Widget"}}
            </script>
          </head>
          <body><main>{body}</main></body>
        </html>
        """.format(body="Helpful product details for agents. " * 50)
        markdown = "# Widget\n\n" + "Helpful Markdown export with schema.org JSON-LD @type hints. " * 20

        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            if url == page_url:
                return FetchResult(url, url, 200, {"content-type": "text/html"}, page_html, 1, [])
            if url == "https://example.com/products/widget.md":
                return FetchResult(url, url, 200, {"content-type": "text/markdown"}, markdown, 1, [])
            return FetchResult(url, url, 404, {"content-type": "text/plain"}, "not found", 1, [])

        with patch("app.checks.search_discovery.fetch_url", new=fake_fetch):
            result = await _sample_pages_have_discovery_metadata(
                self._context(),
                [SitemapEntry(loc=page_url, lastmod="2026-06-01")],
            )

        self.assertTrue(result.ok)
        self.assertIn("sampled pages include titles", result.positives)
        self.assertIn("sampled pages include parseable JSON-LD", result.positives)
        self.assertIn("1 sampled page Markdown alternate or index.md export(s) are usable", result.positives)
        self.assertEqual(result.caveats, [])

    async def test_sampled_page_metadata_reports_missing_metadata(self) -> None:
        page_url = "https://example.com/thin"
        page_html = """
        <html>
          <head><meta name="robots" content="noindex"></head>
          <body><main>Thin page.</main></body>
        </html>
        """

        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            return FetchResult(url, url, 200, {"content-type": "text/html"}, page_html, 1, [])

        with patch("app.checks.search_discovery.fetch_url", new=fake_fetch):
            result = await _sample_pages_have_discovery_metadata(
                self._context(),
                [SitemapEntry(loc=page_url, lastmod=None)],
            )

        self.assertFalse(result.ok)
        self.assertIn("1 sampled sitemap URL(s) are noindex/none", result.issues)
        self.assertIn("1 sampled page(s) are missing a title", result.issues)
        self.assertIn("1 sampled page(s) are missing a canonical link", result.issues)
        self.assertIn("no sampled pages expose parseable JSON-LD", result.issues)
        self.assertIn("1 sampled page(s) have very little extractable text", result.issues)


class HreflangValidationTests(unittest.IsolatedAsyncioTestCase):
    def _context(self, homepage_html: str, final_url: str = "https://example.com/en/") -> AuditContext:
        return make_audit_context(
            homepage_html=homepage_html,
            homepage_requested_url=final_url,
            homepage_final_url=final_url,
        )

    async def test_absent_hreflang_is_neutral(self) -> None:
        result = await _validate_hreflang(
            self._context("<html><head><title>Example</title></head><body>Example</body></html>"),
            [],
        )

        self.assertFalse(result.present)
        self.assertEqual(result.issues, [])
        self.assertEqual(result.caveats, [])

    async def test_accepts_valid_reciprocal_hreflang(self) -> None:
        english_html = """
        <html><head>
          <link rel="alternate" hreflang="en" href="https://example.com/en/">
          <link rel="alternate" hreflang="fr" href="https://example.com/fr/">
          <link rel="alternate" hreflang="x-default" href="https://example.com/">
        </head><body>English page</body></html>
        """
        french_html = """
        <html><head>
          <link rel="alternate" hreflang="en" href="https://example.com/en/">
          <link rel="alternate" hreflang="fr" href="https://example.com/fr/">
          <link rel="alternate" hreflang="x-default" href="https://example.com/">
        </head><body>French page</body></html>
        """

        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            if url == "https://example.com/fr/":
                return make_fetch_result(url, french_html, headers={"content-type": "text/html"})
            return make_fetch_result(url, "not found", status_code=404, headers={"content-type": "text/plain"})

        with patch("app.checks.search_discovery.fetch_url", new=fake_fetch):
            result = await _validate_hreflang(self._context(english_html), [])

        self.assertTrue(result.present)
        self.assertEqual(result.issues, [])
        self.assertIn("sampled hreflang uses valid language tags, self-references, and absolute URLs", result.positives)
        self.assertIn("sampled same-site hreflang alternates are reciprocal where checked", result.positives)

    async def test_reports_invalid_hreflang_values_and_relative_urls(self) -> None:
        html = """
        <html><head>
          <link rel="alternate" hreflang="english" href="/fr/">
        </head><body>English page</body></html>
        """

        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            return make_fetch_result(url, "not found", status_code=404, headers={"content-type": "text/plain"})

        with patch("app.checks.search_discovery.fetch_url", new=fake_fetch):
            result = await _validate_hreflang(self._context(html), [])

        self.assertTrue(result.present)
        self.assertIn("1 hreflang value(s) are not valid language-region tags or x-default", result.issues)
        self.assertIn("1 hreflang URL(s) are not absolute HTTP(S) URLs", result.issues)
        self.assertIn("1 sampled page(s) publish hreflang without a self-reference", result.issues)


class CrawlerRegistryTests(unittest.TestCase):
    def test_duckduckgo_crawlers_are_staged_with_official_source_metadata(self) -> None:
        crawlers = {crawler.name: crawler for crawler in CRAWLERS}
        user_agents = bot_user_agents()

        duckduckbot = crawlers["DuckDuckBot"]
        self.assertEqual(duckduckbot.provider, "DuckDuckGo")
        self.assertEqual(duckduckbot.purpose, "search and retrieval")
        self.assertEqual(
            duckduckbot.fetch_user_agent,
            "DuckDuckBot/1.1; (+http://duckduckgo.com/duckduckbot.html)",
        )
        self.assertEqual(
            duckduckbot.official_documentation_url,
            "https://duckduckgo.com/duckduckgo-help-pages/results/duckduckbot",
        )
        self.assertEqual(
            duckduckbot.official_ip_list_url,
            "https://duckduckgo.com/duckduckbot.json",
        )
        self.assertIn("cannot authenticate official DuckDuckGo traffic", duckduckbot.caveat)
        self.assertIn("IP verification", duckduckbot.caveat)

        duckassist = crawlers["DuckAssistBot"]
        self.assertEqual(duckassist.provider, "DuckDuckGo")
        self.assertEqual(duckassist.purpose, "search and retrieval")
        self.assertEqual(
            duckassist.fetch_user_agent,
            "DuckAssistBot/1.2; (+http://duckduckgo.com/duckassistbot.html)",
        )
        self.assertEqual(
            duckassist.official_documentation_url,
            "https://duckduckgo.com/duckduckgo-help-pages/results/duckassistbot",
        )
        self.assertEqual(
            duckassist.official_ip_list_url,
            "https://duckduckgo.com/duckassistbot.json",
        )
        self.assertIn("cannot authenticate official DuckDuckGo traffic", duckassist.caveat)
        self.assertIn("real-time retrieval for AI-assisted answers", duckassist.caveat)
        self.assertIn("not model training", duckassist.caveat)
        self.assertIn(
            "does not affect organic search ranking or result inclusion",
            duckassist.caveat,
        )
        self.assertIn("up to 72 hours", duckassist.caveat)

        self.assertEqual(user_agents["DuckDuckBot"], duckduckbot.fetch_user_agent)
        self.assertEqual(user_agents["DuckAssistBot"], duckassist.fetch_user_agent)

    def test_active_duckduckgo_crawlers_change_consumer_membership(self) -> None:
        duckduckgo_names = {"DuckDuckBot", "DuckAssistBot"}
        scored_names = tuple(crawler.name for crawler in robots_scoring_crawlers())
        groups = crawler_groups()

        self.assertEqual(
            scored_names,
            (
                "Googlebot",
                "Bingbot",
                "DuckDuckBot",
                "DuckAssistBot",
                "OAI-SearchBot",
                "Claude-SearchBot",
                "PerplexityBot",
                "GPTBot",
                "ClaudeBot",
                "CCBot",
                "Google-Extended",
                "ChatGPT-User",
                "Claude-User",
                "Perplexity-User",
                "Googlebot-Image",
            ),
        )
        self.assertEqual(len(scored_names), 15)
        self.assertTrue(duckduckgo_names.issubset(scored_names))
        grouped_names = {name for names in groups.values() for name in names}
        self.assertTrue(duckduckgo_names.issubset(grouped_names))
        self.assertEqual(sum(len(names) for names in groups.values()), 15)

        self.assertEqual(
            fetch_probe_names(),
            (
                "Googlebot",
                "Bingbot",
                "DuckDuckBot",
                "DuckAssistBot",
                "OAI-SearchBot",
                "PerplexityBot",
                "GPTBot",
                "ClaudeBot",
                "CCBot",
            ),
        )
        self.assertTrue(duckduckgo_names.issubset(fetch_probe_names()))

        directive_sources = crawler_directive_sources()
        self.assertEqual(len(directive_sources), 17)
        self.assertTrue(
            {name.lower() for name in duckduckgo_names}.issubset(directive_sources)
        )

    def test_registry_groups_current_ai_crawlers_without_scoring_brave(self) -> None:
        scored_tokens = {crawler.token for crawler in robots_scoring_crawlers()}
        groups = crawler_groups()

        self.assertIn("OAI-SearchBot", groups["search and retrieval"])
        self.assertIn("Claude-SearchBot", groups["search and retrieval"])
        self.assertIn("PerplexityBot", groups["search and retrieval"])
        self.assertIn("GPTBot", groups["training and corpus"])
        self.assertIn("ClaudeBot", groups["training and corpus"])
        self.assertIn("CCBot", groups["training and corpus"])
        self.assertIn("ChatGPT-User", groups["user-triggered agents"])
        self.assertIn("Claude-User", groups["user-triggered agents"])
        self.assertIn("Perplexity-User", groups["user-triggered agents"])
        self.assertIn("Googlebot-Image", groups["media search"])
        self.assertNotIn("BraveBot", scored_tokens)
        self.assertIn("Brave Search", crawler_registry_caveat())

    def test_fetch_probe_matrix_excludes_control_only_and_unverified_tokens(self) -> None:
        probe_names = set(fetch_probe_names())
        user_agents = bot_user_agents()

        self.assertIn("OAI-SearchBot", probe_names)
        self.assertIn("PerplexityBot", probe_names)
        self.assertNotIn("Google-Extended", probe_names)
        self.assertNotIn("Claude-SearchBot", probe_names)
        self.assertNotIn("BraveBot", probe_names)
        self.assertNotIn("BraveBot", user_agents)
        self.assertIn("OAI-SearchBot", user_agents["OAI-SearchBot"])

    def test_indexing_directive_sources_come_from_registry(self) -> None:
        directive_sources = crawler_directive_sources()

        self.assertIn("claude-searchbot", directive_sources)
        self.assertIn("googlebot-news", directive_sources)
        self.assertIn("perplexity-user", directive_sources)
        self.assertNotIn("bravebot", directive_sources)


class RobotsTests(unittest.TestCase):
    def test_distinguishes_explicit_block_from_wildcard_allow(self) -> None:
        robots = """
        User-agent: GPTBot
        Disallow: /

        User-agent: *
        Allow: /
        """
        access = _classify_bot_access(robots, "https://example.com/")

        self.assertEqual(access["GPTBot"], "explicit_blocked")
        self.assertEqual(access["ClaudeBot"], "wildcard_allowed")
        self.assertEqual(access["Claude-SearchBot"], "wildcard_allowed")
        self.assertNotIn("BraveBot", access)

    def test_reads_agent_policy_signals(self) -> None:
        robots = """
        User-agent: GPTBot
        Allow: /
        Content-Signal: search=yes, ai-input=no, ai-train=no
        """

        self.assertEqual(explicit_ai_bot_count(robots), 1)
        self.assertEqual(content_signal_tokens(robots), ["ai-input", "ai-train", "search"])

    def test_counts_current_anthropic_tokens_but_not_unverified_brave(self) -> None:
        robots = """
        User-agent: Claude-SearchBot
        Allow: /

        User-agent: Claude-User
        Allow: /

        User-agent: BraveBot
        Allow: /
        """

        self.assertEqual(explicit_ai_bot_count(robots), 2)

    def test_reads_response_content_signal_headers(self) -> None:
        headers = {"Content-Signal": "search=yes, tdm-reservation=1"}

        self.assertEqual(response_content_signal_tokens(headers), ["search", "tdm-reservation"])

    def test_reports_robots_quality_signals(self) -> None:
        robots = """
        User-agent: GPTBot
        Sitemap: /sitemap.xml
        Crawl-delay: nope
        Broken directive
        """

        signals = robots_quality_signals(robots)

        self.assertEqual(signals.empty_group_count, 1)
        self.assertEqual(signals.invalid_sitemap_count, 1)
        self.assertEqual(signals.crawl_delay_count, 1)
        self.assertEqual(signals.invalid_crawl_delay_count, 1)
        self.assertEqual(signals.malformed_line_count, 1)

    def test_flags_oversized_robots_file(self) -> None:
        robots = "User-agent: *\nAllow: /\n" + ("#" * (501 * 1024))

        self.assertTrue(robots_quality_signals(robots).oversized)


class RobotsCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_check_includes_quality_and_response_content_signal(self) -> None:
        context = make_audit_context(
            homepage_headers={"Content-Signal": "search=yes"},
            robots_text="User-agent: *\nAllow: /\nSitemap: /sitemap.xml",
            robots_headers={},
            sitemap_text=BASIC_SITEMAP,
        )

        result = await check_robots(context)

        self.assertEqual(result.state, "partial")
        self.assertEqual(result.score, 5)
        self.assertIn("HTTP Content-Signal headers", result.finding)
        self.assertIn("invalid Sitemap directive", result.finding)
        self.assertIn("absolute HTTP(S) URLs", result.fix)


class BotAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_access_probe_matrix_uses_registry_caveats(self) -> None:
        body = "<html><body><main>" + ("Accessible content for agents. " * 120) + "</main></body></html>"
        context = make_audit_context(homepage_html=body)
        requested_user_agents: list[str] = []

        async def fake_fetch(url: str, user_agent: str = "", **kwargs: object) -> FetchResult:
            requested_user_agents.append(user_agent)
            return FetchResult(
                requested_url=url,
                final_url=url,
                status_code=200,
                headers={"content-type": "text/html"},
                text=body,
                elapsed_ms=1,
                redirect_chain=[],
            )

        with patch("app.checks.bot_access.fetch_url", new=fake_fetch):
            result = await check_bot_access(context)

        self.assertEqual(result.state, "pass")
        self.assertEqual(result.score, 6)
        self.assertIn("Probe caveat", result.finding)
        self.assertIn("No material browser-vs-bot routing differences", result.finding)
        self.assertIn("Brave Search", result.finding)
        self.assertFalse(any("Brave" in user_agent for user_agent in requested_user_agents))

    async def test_bot_access_reports_routing_differences_without_penalty(self) -> None:
        browser_body = (
            '<html><head><link rel="canonical" href="https://example.com/"></head>'
            "<body><main>"
            + ("Browser-visible content for agents and search. " * 75)
            + "</main></body></html>"
        )
        bot_body = (
            '<html><head><link rel="canonical" href="https://example.com/bot"></head>'
            "<body><main>"
            + ("Bot-routed content remains readable. " * 60)
            + "</main></body></html>"
        )
        context = make_audit_context(
            homepage_html=browser_body,
            homepage_final_url=f"{EXAMPLE_URL}/",
        )

        async def fake_fetch(url: str, user_agent: str = "", **kwargs: object) -> FetchResult:
            if "OAI-SearchBot" in user_agent:
                return FetchResult(
                    requested_url=url,
                    final_url="https://example.com/bot",
                    status_code=206,
                    headers={"content-type": "text/html"},
                    text=bot_body,
                    elapsed_ms=1,
                    redirect_chain=[],
                )
            return FetchResult(
                requested_url=url,
                final_url="https://example.com/",
                status_code=200,
                headers={"content-type": "text/html"},
                text=browser_body,
                elapsed_ms=1,
                redirect_chain=[],
            )

        with patch("app.checks.bot_access.fetch_url", new=fake_fetch):
            result = await check_bot_access(context)

        self.assertEqual(result.state, "pass")
        self.assertEqual(result.score, 6)
        self.assertIn("Inferred browser-vs-bot routing differences", result.finding)
        self.assertIn("OAI-SearchBot", result.finding)
        self.assertIn("status 206 vs browser 200", result.finding)
        self.assertIn("final URL example.com/bot vs browser example.com", result.finding)
        self.assertIn("canonical example.com/bot vs browser example.com", result.finding)
        self.assertIn("visible words", result.finding)

    async def test_bot_access_ignores_recaptcha_script_tag_in_body(self) -> None:
        """F4-06: A Google reCAPTCHA ``<script src="…/recaptcha/api.js">`` tag
        embedded in a normal page is not a challenge page.

        Before the fix the body substring scan matched the literal
        ``captcha`` substring (e.g. inside ``/recaptcha/api.js``) and reported
        every bot probe as ``challenge page`` → score 0/6. The fix narrows
        ``captcha`` detection to the page ``<title>`` element only, so a normal
        page that loads reCAPTCHA on a contact form must score 6/6.
        """
        body = (
            "<html><head><title>Acme Robotics — Contact us</title></head>"
            "<body><main>"
            + ("Accessible content for agents and search engines. " * 80)
            + '</main>'
            '<script src="https://www.google.com/recaptcha/api.js" async defer></script>'
            '<div class="g-recaptcha" data-sitekey="abc123"></div>'
            "</body></html>"
        )
        context = make_audit_context(homepage_html=body)

        async def fake_fetch(url: str, user_agent: str = "", **kwargs: object) -> FetchResult:
            return FetchResult(
                requested_url=url,
                final_url=url,
                status_code=200,
                headers={"content-type": "text/html"},
                text=body,
                elapsed_ms=1,
                redirect_chain=[],
            )

        with patch("app.checks.bot_access.fetch_url", new=fake_fetch):
            result = await check_bot_access(context)

        self.assertEqual(result.state, "pass")
        self.assertEqual(result.score, 6)
        self.assertNotIn("challenge page", result.finding)

    async def test_bot_access_ignores_captcha_substring_in_response_headers(self) -> None:
        """F4-06: ``captcha`` must not be matched inside HTTP response headers.

        Content-Security-Policy and other response headers can legitimately
        include ``captcha`` as a directive keyword (e.g. ``script-src …/recaptcha/``).
        Scanning header values used to flag every bot probe as ``challenge page``.
        The fix removes header scanning for the captcha path by routing
        captcha detection through ``_has_captcha_title`` only.
        """
        body = (
            "<html><head><title>Acme Robotics — Home</title></head>"
            "<body><main>"
            + ("Accessible content for agents and search engines. " * 80)
            + "</main></body></html>"
        )
        # CSP and content-security headers reference /recaptcha/ in script-src —
        # a normal pattern for any site that uses Google reCAPTCHA.
        headers_with_captcha = {
            "content-type": "text/html",
            "content-security-policy": (
                "default-src 'self'; script-src 'self' https://www.google.com/recaptcha/ "
                "https://www.gstatic.com/recaptcha/ 'unsafe-inline'; "
                "frame-src https://www.google.com/recaptcha/"
            ),
            "link": "<https://www.google.com/recaptcha/about/>; rel=preconnect",
        }
        context = make_audit_context(
            homepage_html=body,
            homepage_headers=headers_with_captcha,
        )

        async def fake_fetch(url: str, user_agent: str = "", **kwargs: object) -> FetchResult:
            return FetchResult(
                requested_url=url,
                final_url=url,
                status_code=200,
                headers=dict(headers_with_captcha),
                text=body,
                elapsed_ms=1,
                redirect_chain=[],
            )

        with patch("app.checks.bot_access.fetch_url", new=fake_fetch):
            result = await check_bot_access(context)

        self.assertEqual(result.state, "pass")
        self.assertEqual(result.score, 6)
        self.assertNotIn("challenge page", result.finding)

    async def test_bot_access_baseline_suppresses_site_wide_challenge(self) -> None:
        """F4-06: If the browser baseline ALSO matches a challenge signature,
        the finding is a site-wide pattern, not bot discrimination, and is
        suppressed for every bot probe.
        """
        challenge_body = (
            "<html><head><title>Just a moment…</title></head>"
            "<body>"
            "<h1>cf-chl cf-chl-bypass</h1>"
            "<p>cloudflare ray id: 8a1b2c3d4e5f — checking your browser</p>"
            "</body></html>"
        )
        context = make_audit_context(homepage_html=challenge_body)

        async def fake_fetch(url: str, user_agent: str = "", **kwargs: object) -> FetchResult:
            # Same body for browser and every bot — site-wide challenge page,
            # not bot discrimination.
            return FetchResult(
                requested_url=url,
                final_url=url,
                status_code=200,
                headers={"content-type": "text/html"},
                text=challenge_body,
                elapsed_ms=1,
                redirect_chain=[],
            )

        with patch("app.checks.bot_access.fetch_url", new=fake_fetch):
            result = await check_bot_access(context)

        # The browser got the same challenge page, so no bot-specific finding.
        self.assertEqual(result.state, "pass")
        self.assertEqual(result.score, 6)
        self.assertNotIn("challenge page", result.finding)

    async def test_bot_access_still_flags_real_captcha_title_for_bots(self) -> None:
        """F4-06 positive case: a real CAPTCHA challenge page (with ``captcha``
        in the title) MUST still be flagged — but only for bots that see it
        when the browser probe did NOT see the same challenge.

        Mixes the matrix: 1 bot (Googlebot) sees the clean page and stays
        accessible, the remaining bots see the challenge page. Result: state
        is ``partial`` and the finding lists ``(challenge page)`` per bot.
        """
        challenge_body = (
            "<html><head><title>reCAPTCHA verification required</title></head>"
            "<body>"
            + ("Verify you are human. " * 40)
            + "</body></html>"
        )
        clean_body = (
            "<html><head><title>Acme Robotics — Home</title></head>"
            "<body><main>"
            + ("Accessible content for agents and search engines. " * 80)
            + "</main></body></html>"
        )
        context = make_audit_context(homepage_html=clean_body)

        async def fake_fetch(url: str, user_agent: str = "", **kwargs: object) -> FetchResult:
            # Bots get the challenge page; the browser probe gets the clean page.
            # The browser user-agent is identified by exact-match so that the
            # OAI-SearchBot / PerplexityBot / GPTBot / Bingbot tokens — many of
            # which contain "Mozilla" or "Chrome" — fall through to the
            # challenge branch.
            if user_agent == BROWSER_USER_AGENT:
                return FetchResult(
                    requested_url=url,
                    final_url=url,
                    status_code=200,
                    headers={"content-type": "text/html"},
                    text=clean_body,
                    elapsed_ms=1,
                    redirect_chain=[],
                )
            # Googlebot gets the clean page (matrix mix); every other bot gets
            # the captcha challenge.
            if "Googlebot" in user_agent and "Image" not in user_agent and "Video" not in user_agent and "News" not in user_agent:
                return FetchResult(
                    requested_url=url,
                    final_url=url,
                    status_code=200,
                    headers={"content-type": "text/html"},
                    text=clean_body,
                    elapsed_ms=1,
                    redirect_chain=[],
                )
            return FetchResult(
                requested_url=url,
                final_url=url,
                status_code=200,
                headers={"content-type": "text/html"},
                text=challenge_body,
                elapsed_ms=1,
                redirect_chain=[],
            )

        with patch("app.checks.bot_access.fetch_url", new=fake_fetch):
            result = await check_bot_access(context)

        # At least one bot is still accessible (Googlebot) and at least one
        # bot must be flagged as a challenge page. ``partial`` exposes the
        # per-bot reason list, so we can assert the literal ``(challenge page)``
        # string is present.
        self.assertEqual(result.state, "partial")
        self.assertIn("(challenge page)", result.finding)
        # 8 of 9 probe bots are flagged: only Googlebot is accessible.
        # Score is round(1/9 * 6) = 1.
        self.assertEqual(result.score, 1)

    async def test_bot_access_baseline_suppresses_captcha_title(self) -> None:
        """F4-06: a title that literally contains ``captcha`` does NOT trigger
        the bot-only finding when the browser baseline also contains
        ``captcha`` in its title (site-wide pattern, not bot discrimination).
        """
        captcha_body = (
            "<html><head><title>reCAPTCHA challenge</title></head>"
            "<body><main>"
            + ("Site-wide captcha announcement that everyone sees. " * 80)
            + "</main></body></html>"
        )
        context = make_audit_context(homepage_html=captcha_body)

        async def fake_fetch(url: str, user_agent: str = "", **kwargs: object) -> FetchResult:
            return FetchResult(
                requested_url=url,
                final_url=url,
                status_code=200,
                headers={"content-type": "text/html"},
                text=captcha_body,
                elapsed_ms=1,
                redirect_chain=[],
            )

        with patch("app.checks.bot_access.fetch_url", new=fake_fetch):
            result = await check_bot_access(context)

        # The browser also got the captcha title — site-wide, not bot-only.
        self.assertEqual(result.state, "pass")
        self.assertEqual(result.score, 6)
        self.assertNotIn("challenge page", result.finding)


class MachineSurfaceTests(unittest.IsolatedAsyncioTestCase):
    def _context(self, homepage_html: str, headers: dict[str, str] | None = None) -> AuditContext:
        return make_audit_context(
            homepage_html=homepage_html,
            homepage_headers=headers or None,
            homepage_final_url=f"{EXAMPLE_URL}/",
        )

    async def test_warns_when_default_scope_has_no_optional_surfaces(self) -> None:
        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            return FetchResult(url, url, 404, {"content-type": "text/plain"}, "not found", 1, [])

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=fake_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
        ):
            result = await check_machine_surfaces(self._context("<html><body>Example</body></html>"))

        self.assertEqual(result.state, "warn")
        self.assertEqual(result.score, 0)
        self.assertIn("across 5 included discovery families", result.finding)
        self.assertIn("/llms-full.txt", result.fix)
        self.assertNotIn("OpenAPI/API Catalog", result.fix)

    async def test_optional_scope_toggles_expand_only_selected_machine_families(self) -> None:
        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            return FetchResult(url, url, 404, {"content-type": "text/plain"}, "not found", 1, [])

        scenarios = (
            (
                {},
                "across 5 included discovery families",
                ("OpenAPI/API Catalog", "OAuth metadata", "commerce protocol metadata"),
            ),
            (
                {"include_protocols": True},
                "across 14 included discovery families",
                ("OAuth metadata", "commerce protocol metadata"),
            ),
            (
                {"include_account_auth": True},
                "across 8 included discovery families",
                ("OpenAPI/API Catalog", "commerce protocol metadata"),
            ),
            (
                {"include_ecommerce": True},
                "across 7 included discovery families",
                ("OpenAPI/API Catalog", "OAuth metadata"),
            ),
        )

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=fake_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
        ):
            for kwargs, expected_count, excluded_recommendations in scenarios:
                with self.subTest(scope=kwargs):
                    result = await check_machine_surfaces(
                        self._context("<html><body>Example</body></html>"),
                        **kwargs,
                    )

                self.assertEqual(result.state, "warn")
                self.assertIn(expected_count, result.finding)
                for excluded_recommendation in excluded_recommendations:
                    self.assertNotIn(excluded_recommendation, result.fix)

    async def test_partial_default_scope_reports_excluded_optional_surface_families(self) -> None:
        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            return FetchResult(url, url, 404, {"content-type": "text/plain"}, "not found", 1, [])

        headers = {
            "content-type": "text/html",
            "link": '<https://example.com/.well-known/api-catalog>; rel="api-catalog"',
        }

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=fake_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
        ):
            result = await check_machine_surfaces(
                self._context("<html><body>Example</body></html>", headers=headers)
            )

        self.assertEqual(result.state, "partial")
        self.assertEqual(result.score, 1)
        self.assertIn("Link header discovery", result.finding)
        self.assertIn("Essentials checked 5 discovery families selected for this audit", result.finding)
        self.assertIn("API/protocol surfaces, account/auth surfaces, commerce surfaces were excluded", result.finding)

    async def test_full_scope_passes_with_protocol_account_and_commerce_surfaces(self) -> None:
        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            if url.endswith("/.well-known/api-catalog"):
                return FetchResult(
                    url,
                    url,
                    200,
                    {"content-type": "application/json"},
                    '{"name":"MachineRead API catalog","items":["api"]}',
                    1,
                    [],
                )
            if url.endswith("/.well-known/oauth-authorization-server"):
                return FetchResult(
                    url,
                    url,
                    200,
                    {"content-type": "application/json"},
                    '{"issuer":"https://example.com","authorization_endpoint":"https://example.com/oauth"}',
                    1,
                    [],
                )
            if url.endswith("/.well-known/x402"):
                return FetchResult(
                    url,
                    url,
                    200,
                    {"content-type": "application/json"},
                    '{"name":"Commerce metadata","accepts":["x402"]}',
                    1,
                    [],
                )
            return FetchResult(url, url, 404, {"content-type": "text/plain"}, "not found", 1, [])

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=fake_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
        ):
            result = await check_machine_surfaces(
                self._context("<html><body>Example</body></html>"),
                include_protocols=True,
                include_account_auth=True,
                include_ecommerce=True,
            )

        self.assertEqual(result.state, "pass")
        self.assertEqual(result.score, 3)
        self.assertIn("API catalog", result.finding)
        self.assertIn("OAuth authorization metadata", result.finding)
        self.assertIn("agentic commerce metadata", result.finding)


class AgentReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_strict_lens_separates_agent_native_signals(self) -> None:
        context = make_audit_context(
            homepage_headers={},
            robots_text=ROBOTS_WITH_SITEMAP,
            sitemap_text=BASIC_SITEMAP,
        )

        with (
            patch("app.agent_readiness.best_sitemap", new=AsyncMock(return_value=(True, 1, False, False, True))),
            patch("app.agent_readiness.agent_text_access", new=AsyncMock(return_value=(False, []))),
            patch("app.agent_readiness._json_surface_available", new=AsyncMock(return_value=False)),
            patch("app.agent_readiness._auth_md_available", new=AsyncMock(return_value=False)),
            patch("app.agent_readiness._has_dns_aid", new=AsyncMock(return_value=False)),
        ):
            summary = await build_agent_readiness_summary(context)

        self.assertEqual(summary.score, 25)
        self.assertEqual(summary.earned, 2)
        self.assertEqual(summary.max, 8)
        self.assertIn("robots.txt published", summary.passed)
        self.assertIn("llms.txt or Markdown negotiation", summary.missing)
        self.assertIn("API Catalog excluded by audit scope.", summary.not_checked)
        self.assertIn("explicit agent-native discovery", summary.caveat)

    async def test_full_scope_exposes_a2a_and_split_commerce_protocols(self) -> None:
        context = make_audit_context(
            homepage_headers={},
            robots_text=(
                "User-agent: GPTBot\nAllow: /\n"
                "Content-Signal: search=yes\n"
                "Sitemap: https://example.com/sitemap.xml"
            ),
            sitemap_text=BASIC_SITEMAP,
        )

        async def fake_json_surface(
            _context: AuditContext,
            paths: tuple[str, ...],
            _accept: str,
            _require_keys: tuple[str, ...] | None = None,
        ) -> bool:
            return "/.well-known/agent-card.json" in paths or "/.well-known/x402" in paths

        with (
            patch("app.agent_readiness.best_sitemap", new=AsyncMock(return_value=(True, 1, False, False, True))),
            patch("app.agent_readiness.agent_text_access", new=AsyncMock(return_value=(True, []))),
            patch("app.agent_readiness._json_surface_available", new=fake_json_surface),
            patch("app.agent_readiness._auth_md_available", new=AsyncMock(return_value=True)),
            patch("app.agent_readiness._has_dns_aid", new=AsyncMock(return_value=False)),
        ):
            summary = await build_agent_readiness_summary(
                context,
                include_protocols=True,
                include_account_auth=True,
                include_ecommerce=True,
            )

        self.assertEqual(summary.max, 21)
        self.assertEqual(summary.earned, 8)
        self.assertEqual(summary.score, 38)
        self.assertIn("A2A Agent Card", summary.passed)
        self.assertIn("x402 payment metadata", summary.passed)
        self.assertIn("MPP commerce metadata", summary.missing)
        self.assertIn("UCP commerce metadata", summary.missing)
        self.assertIn("ACP commerce metadata", summary.missing)
        commerce = next(category for category in summary.categories if category.name == "Commerce")
        self.assertEqual(commerce.max, 4)
        self.assertEqual(commerce.earned, 1)
        self.assertEqual(summary.benchmark.max, 21)

    async def test_scope_toggles_include_only_relevant_agent_readiness_probes(self) -> None:
        context = make_audit_context(
            homepage_headers={},
            robots_text=ROBOTS_WITH_SITEMAP,
            sitemap_text=BASIC_SITEMAP,
        )
        scenarios = (
            ({}, 8, "API Catalog", "OAuth/OIDC discovery metadata", "x402 payment metadata"),
            ({"include_protocols": True}, 14, None, "OAuth/OIDC discovery metadata", "x402 payment metadata"),
            ({"include_account_auth": True}, 11, "API Catalog", None, "x402 payment metadata"),
            ({"include_ecommerce": True}, 12, "API Catalog", "OAuth/OIDC discovery metadata", None),
            (
                {"include_protocols": True, "include_account_auth": True, "include_ecommerce": True},
                21,
                None,
                None,
                None,
            ),
        )

        with (
            patch("app.agent_readiness.best_sitemap", new=AsyncMock(return_value=(True, 1, False, False, True))),
            patch("app.agent_readiness.agent_text_access", new=AsyncMock(return_value=(False, []))),
            patch("app.agent_readiness._json_surface_available", new=AsyncMock(return_value=False)),
            patch("app.agent_readiness._auth_md_available", new=AsyncMock(return_value=False)),
            patch("app.agent_readiness._has_dns_aid", new=AsyncMock(return_value=False)),
        ):
            for kwargs, expected_max, protocol_excluded, auth_excluded, commerce_excluded in scenarios:
                with self.subTest(scope=kwargs):
                    summary = await build_agent_readiness_summary(context, **kwargs)

                self.assertEqual(summary.max, expected_max)
                self.assertEqual(summary.benchmark.max, expected_max)
                not_checked = set(summary.not_checked)
                for excluded_label in (
                    protocol_excluded,
                    auth_excluded,
                    commerce_excluded,
                ):
                    if excluded_label is None:
                        continue
                    self.assertIn(f"{excluded_label} excluded by audit scope.", not_checked)
                included_labels = {
                    label
                    for label in ("API Catalog", "OAuth/OIDC discovery metadata", "x402 payment metadata")
                    if label not in (protocol_excluded, auth_excluded, commerce_excluded)
                }
                for included_label in included_labels:
                    self.assertNotIn(f"{included_label} excluded by audit scope.", not_checked)


class EntityCacheTests(unittest.TestCase):
    def test_reads_fresh_cache_without_public_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "entity.sqlite3"
            with patch.dict(
                "os.environ",
                {"ENTITY_CACHE_PATH": str(cache_path), "ENTITY_CACHE_TTL_DAYS": "30"},
            ):
                set_cached_entity_lookup(
                    cache_key="example.com",
                    status="neither",
                    state="fail",
                    evidence_level="verified",
                    score=0,
                    max_score=10,
                    finding="No entity found.",
                    fix="Build earned citations.",
                )

                cached = get_cached_entity_lookup("example.com")

        self.assertIsNotNone(cached)
        assert cached is not None
        self.assertEqual(cached.status, "neither")
        self.assertEqual(cached.finding, "No entity found.")

    def test_expired_cache_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "entity.sqlite3"
            with patch.dict(
                "os.environ",
                {"ENTITY_CACHE_PATH": str(cache_path), "ENTITY_CACHE_TTL_DAYS": "0"},
            ):
                set_cached_entity_lookup(
                    cache_key="example.com",
                    status="both",
                    state="pass",
                    evidence_level="verified",
                    score=10,
                    max_score=10,
                    finding="Entity found.",
                    fix="No action needed.",
                )

                cached = get_cached_entity_lookup("example.com")

        self.assertIsNone(cached)


class RubricTests(unittest.TestCase):
    def _calibrated_essentials_checks(self) -> list[CheckResult]:
        return [
            CheckResult(
                pillar=group.pillar,
                check_name=group.check_name,
                label=group.check_name,
                state="pass",
                score=group.max_score,
                max_score=group.max_score,
                finding="Included group passed.",
                fix="No action needed.",
                effort="low",
            )
            for group in ESSENTIALS_CHECK_GROUPS
        ]

    def _agent_readiness_summary(self) -> AgentReadinessSummary:
        return AgentReadinessSummary(
            score=100,
            earned=8,
            max=8,
            label="Agent-native signals present",
            categories=[],
            passed=[],
            missing=[],
            not_checked=[],
            benchmark=build_agent_benchmark_comparison(score=100, earned=8, maximum=8),
            caveat="Strict agent-native readiness lens.",
        )

    def test_essentials_check_group_calibration_is_current(self) -> None:
        expected_groups = {
            "social": 2,
            "wikipedia": 4,
            "robots_txt": 6,
            "bot_access": 6,
            "html_structure": 4,
            "schema_ld": 5,
            "llms_txt": 5,
            "ssr": 4,
            "machine_surfaces": 3,
            "pagespeed": 3,
            "canonical": 5,
            "indexing": 5,
            "search_discovery": 4,
        }

        self.assertEqual(ESSENTIALS_CHECK_GROUP_COUNT, 13)
        self.assertEqual(ESSENTIALS_CHECKED_MAX, 56)
        self.assertEqual(
            {group.check_name: group.max_score for group in ESSENTIALS_CHECK_GROUPS},
            expected_groups,
        )

    def test_result_assembly_preserves_essentials_denominator(self) -> None:
        result = build_result(
            "https://example.com",
            self._calibrated_essentials_checks(),
            self._agent_readiness_summary(),
        )
        included = [check for check in result.checks if check.state != "locked"]
        advanced = [check for check in result.checks if check.state == "locked"]

        self.assertEqual(len(included), ESSENTIALS_CHECK_GROUP_COUNT)
        self.assertEqual(sum(check.max_score for check in included), ESSENTIALS_CHECKED_MAX)
        self.assertEqual(result.benchmark.checked_score, ESSENTIALS_CHECKED_MAX)
        self.assertEqual(result.benchmark.checked_max, ESSENTIALS_CHECKED_MAX)
        self.assertEqual(result.benchmark.score, 100)
        self.assertEqual(result.overall_score, ESSENTIALS_CHECKED_MAX)
        self.assertEqual(len(advanced), 9)
        self.assertIn(f"{ESSENTIALS_CHECKED_MAX} checked points", result.benchmark.caveat)

    def test_result_scope_metadata_matches_every_toggle_combination(self) -> None:
        scenarios = (
            (False, False, False, "General website", {"API and protocol discovery", "Account/auth discovery", "Commerce protocol metadata", "Catalog JSON"}, set()),
            (True, False, False, "General website + API/protocol", {"Account/auth discovery", "Commerce protocol metadata", "Catalog JSON"}, {"API and protocol discovery"}),
            (False, True, False, "General website + account/auth", {"API and protocol discovery", "Commerce protocol metadata", "Catalog JSON"}, {"Account/auth discovery"}),
            (False, False, True, "Commerce storefront", {"API and protocol discovery", "Account/auth discovery"}, {"Commerce protocol metadata", "Catalog JSON"}),
            (True, True, True, "Commerce storefront + API/protocol + account/auth", set(), {"API and protocol discovery", "Account/auth discovery", "Commerce protocol metadata", "Catalog JSON"}),
        )

        for include_protocols, include_account_auth, include_ecommerce, label, excluded, included in scenarios:
            with self.subTest(
                include_protocols=include_protocols,
                include_account_auth=include_account_auth,
                include_ecommerce=include_ecommerce,
            ):
                result = build_result(
                    "https://example.com",
                    self._calibrated_essentials_checks(),
                    self._agent_readiness_summary(),
                    include_protocols=include_protocols,
                    include_account_auth=include_account_auth,
                    include_ecommerce=include_ecommerce,
                )
                included_rows = [check for check in result.checks if check.state != "locked"]
                workflow_row = next(
                    check for check in result.checks if check.check_name == "agent_task_simulation"
                )

                self.assertEqual(result.scope.label, label)
                self.assertEqual(len(included_rows), ESSENTIALS_CHECK_GROUP_COUNT)
                self.assertEqual(sum(check.max_score for check in included_rows), ESSENTIALS_CHECKED_MAX)
                self.assertTrue(excluded.issubset(set(result.scope.excluded_optional_surfaces)))
                self.assertTrue(included.issubset(set(result.scope.included_optional_surfaces)))
                expected_workflow_label = "Agent Commerce Simulation" if include_ecommerce else "Agent Workflow Simulation"
                self.assertEqual(workflow_row.label, expected_workflow_label)
                self.assertIn("same selected Essentials scope", result.benchmark.caveat)

    def test_locked_checks_reserve_full_rubric_points(self) -> None:
        locked_total = sum(check.max_score for check in locked_checks())

        self.assertEqual(locked_total, 44)

    def test_locked_workflow_check_follows_commerce_scope(self) -> None:
        general = next(check for check in locked_checks() if check.check_name == "agent_task_simulation")
        commerce = next(
            check for check in locked_checks(include_ecommerce=True)
            if check.check_name == "agent_task_simulation"
        )

        self.assertEqual(general.label, "Agent Workflow Simulation")
        self.assertEqual(commerce.label, "Agent Commerce Simulation")
        self.assertIn("conversion", general.finding)
        self.assertIn("checkout", commerce.finding)

    def test_benchmark_comparison_uses_free_evidence_score(self) -> None:
        checks = [
            CheckResult(
                pillar="scrapability",
                check_name="html_structure",
                label="Semantic HTML",
                state="pass",
                score=28,
                max_score=40,
                finding="Readable HTML.",
                fix="No action needed.",
                effort="low",
            ),
            CheckResult(
                pillar="seo",
                check_name="multi_engine_index_coverage",
                label="Index coverage",
                state="locked",
                evidence_level="not_applicable",
                available_in="Starter",
                score=0,
                max_score=20,
                finding="Locked.",
                fix="Upgrade to verify.",
                effort="medium",
            ),
        ]

        comparison = build_benchmark_comparison(checks)

        self.assertEqual(comparison.score, 70)
        self.assertEqual(comparison.checked_score, 28)
        self.assertEqual(comparison.checked_max, 40)
        self.assertEqual(comparison.benchmark_count, len(comparison.entries))
        self.assertGreaterEqual(comparison.benchmark_count, 14)
        self.assertGreaterEqual(len(comparison.nearest), 1)
        self.assertIn("same selected Essentials scope", comparison.caveat)

    def test_benchmark_comparison_follows_selected_scope(self) -> None:
        checks = [
            CheckResult(
                pillar="scrapability",
                check_name="html_structure",
                label="Semantic HTML",
                state="pass",
                score=28,
                max_score=40,
                finding="Readable HTML.",
                fix="No action needed.",
                effort="low",
            )
        ]

        default_comparison = build_benchmark_comparison(checks)
        full_scope_comparison = build_benchmark_comparison(
            checks,
            include_protocols=True,
            include_account_auth=True,
            include_ecommerce=True,
        )
        default_entry = default_comparison.entries[0]
        full_scope_entry = next(entry for entry in full_scope_comparison.entries if entry.name == default_entry.name)
        agent_comparison = build_agent_benchmark_comparison(
            score=31,
            earned=5,
            maximum=21,
            include_protocols=True,
            include_account_auth=True,
            include_ecommerce=True,
        )

        self.assertEqual(default_comparison.benchmark_count, len(default_comparison.entries))
        self.assertEqual(full_scope_comparison.benchmark_count, len(full_scope_comparison.entries))
        self.assertGreaterEqual(default_comparison.benchmark_count, 14)
        self.assertEqual(default_comparison.benchmark_count, full_scope_comparison.benchmark_count)
        self.assertEqual(default_entry.agent_readiness_max, 8)
        self.assertEqual(full_scope_entry.agent_readiness_max, 21)
        self.assertEqual(agent_comparison.benchmark_count, full_scope_comparison.benchmark_count)
        self.assertEqual(agent_comparison.max, 21)


if __name__ == "__main__":
    unittest.main()
