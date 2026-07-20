import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.checks.extraction_readiness import ExtractionReadinessInput, analyse_extraction_readiness
from app.checks.llms_txt import check_llms_txt
from app.checks.schema_ld import check_schema_ld
from app.checks.search_blurb import SearchBlurbAnalysis
from app.checks.search_discovery import (
    FeedFreshnessResult,
    SamplePageMetadataResult,
    TrustSurfaceResult,
    check_search_discovery,
)
from app.checks.ssr import check_ssr
from app.qa2_evidence import collect_qa2_evidence
from app.rubric import ESSENTIALS_CHECKED_MAX, ESSENTIALS_CHECK_GROUP_COUNT
from backend.tests.fixtures import make_audit_context, make_fetch_result


def _page(title: str, path: str) -> str:
    description = (
        f"{title} explains reliable machine-readable website content with clear "
        "metadata, structured facts, and accessible source text for public crawlers."
    )
    words = " ".join(
        f"reliable machine readable website content metadata structured facts accessible source crawler {index}"
        for index in range(12)
    )
    return f"""
    <html><head>
      <title>{title}</title>
      <meta name="description" content="{description}">
      <meta property="og:description" content="{description}">
      <meta name="twitter:description" content="{description}">
      <link rel="canonical" href="https://example.com{path}">
      <script type="application/ld+json">
        {{"@context":"https://schema.org","@type":"Organization","name":"Example",
        "url":"https://example.com","logo":"https://example.com/logo.png",
        "sameAs":["https://social.example/example"]}}
      </script>
    </head><body><main><h1>{title}</h1><p>{words}</p></main></body></html>
    """


class QA2RuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        sitemap = """
        <urlset>
          <url><loc>https://example.com</loc><lastmod>2026-07-17</lastmod></url>
          <url><loc>https://example.com/about</loc><lastmod>2026-07-17</lastmod></url>
        </urlset>
        """
        self.context = make_audit_context(
            homepage_html=_page("Machine-readable homepage", ""),
            robots_text=(
                "User-agent: *\nAllow: /\nSitemap: https://example.com/sitemap.xml"
            ),
            sitemap_text=sitemap,
        )
        self.calls: list[tuple[str, str]] = []

    async def _fetch(self, url: str, **kwargs):
        self.calls.append((url, kwargs.get("accept", "")))
        if url == "https://example.com/alternate-index.xml":
            return make_fetch_result(
                url,
                """
                <sitemapindex>
                  <sitemap><loc>https://example.com/child-sitemap.xml</loc></sitemap>
                  <sitemap><loc>https://example.com/child-sitemap.xml</loc></sitemap>
                </sitemapindex>
                """,
                headers={"content-type": "application/xml"},
            )
        if url == "https://example.com/child-sitemap.xml":
            return make_fetch_result(
                url,
                """
                <urlset>
                  <url><loc>https://example.com/about</loc><lastmod>2026-07-17</lastmod></url>
                </urlset>
                """,
                headers={"content-type": "application/xml"},
            )
        if url == "https://example.com/llms.txt":
            return make_fetch_result(
                url,
                "# Example\n\nMachine-readable documentation: https://example.com/about",
                headers={"content-type": "text/plain"},
            )
        if url == "https://example.com/about":
            return make_fetch_result(
                url,
                _page("About Machine-readable Example", "/about"),
                headers={"content-type": "text/html"},
            )
        markdown = "# Machine-readable content\n\n" + " ".join(
            "reliable machine readable website content metadata structured facts accessible source crawler"
            for _ in range(8)
        )
        return make_fetch_result(
            url,
            markdown,
            headers={"content-type": "text/markdown", "vary": "Accept"},
        )

    async def _bundle(self, include_ecommerce: bool = False):
        with (
            patch("app.qa2_evidence.fetch_url", new=self._fetch),
            patch("app.checks.sitemap_analysis.fetch_url", new=self._fetch),
        ):
            return await collect_qa2_evidence(self.context, include_ecommerce)

    async def test_bundle_reuses_root_sitemap_homepage_and_sample_fetches(self) -> None:
        bundle = await self._bundle()

        requested_urls = [url for url, _ in self.calls]
        self.assertEqual(requested_urls.count("https://example.com/sitemap.xml"), 0)
        self.assertEqual(requested_urls.count("https://example.com"), 1)
        self.assertEqual(requested_urls.count("https://example.com/about"), 1)
        self.assertEqual(requested_urls.count("https://example.com/about/index.md"), 1)
        self.assertEqual(requested_urls.count("https://example.com/llms.txt"), 1)
        self.assertEqual(len(bundle.search_blurb.pages), 2)
        self.assertEqual(len(bundle.sample_page_responses), 2)

    async def test_fetches_each_alternate_and_child_sitemap_once(self) -> None:
        self.context = make_audit_context(
            homepage_html=_page("Machine-readable homepage", ""),
            robots_text=(
                "User-agent: *\nAllow: /\n"
                "Sitemap: https://example.com/sitemap.xml\n"
                "Sitemap: https://example.com/alternate-index.xml\n"
                "Sitemap: https://example.com/alternate-index.xml"
            ),
            sitemap_text=self.context.sitemap.text,
        )

        bundle = await self._bundle()

        requested_urls = [url for url, _ in self.calls]
        self.assertEqual(requested_urls.count("https://example.com/alternate-index.xml"), 1)
        self.assertEqual(requested_urls.count("https://example.com/child-sitemap.xml"), 1)
        self.assertTrue(bundle.sitemap_sample.is_index)
        self.assertEqual(bundle.sitemap_sample.sitemap_count, 3)

    async def test_reuses_homepage_markdown_by_redirect_final_url(self) -> None:
        redirected_url = "https://example.com/home"
        candidate_url = f"{redirected_url}/index.md"
        sitemap = (
            "<urlset><url><loc>https://example.com/home</loc>"
            "<lastmod>2026-07-17</lastmod></url></urlset>"
        )
        self.context = make_audit_context(
            homepage_html=_page("Machine-readable homepage", "/home"),
            homepage_final_url=redirected_url,
            sitemap_text=sitemap,
        )
        markdown = "# Machine-readable content\n\n" + " ".join(
            "reliable machine readable website content metadata structured facts accessible source crawler"
            for _ in range(8)
        )

        async def redirect_fetch(url: str, **kwargs):
            self.calls.append((url, kwargs.get("accept", "")))
            if url == "https://example.com":
                return make_fetch_result(
                    url,
                    markdown,
                    final_url=redirected_url,
                    headers={"content-type": "text/markdown", "vary": "Accept"},
                )
            if url == "https://example.com/llms.txt":
                return make_fetch_result(
                    url,
                    "# Example\n\nMachine-readable documentation: https://example.com/home",
                    headers={"content-type": "text/plain"},
                )
            self.fail(f"unexpected fetch: {url}")

        with (
            patch("app.qa2_evidence.fetch_url", new=redirect_fetch),
            patch("app.checks.sitemap_analysis.fetch_url", new=redirect_fetch),
        ):
            bundle = await collect_qa2_evidence(self.context)

        requested_urls = [url for url, _ in self.calls]
        self.assertNotIn(candidate_url, requested_urls)
        self.assertEqual(len(bundle.sample_markdown_by_page), 1)
        page_url, response = bundle.sample_markdown_by_page[0]
        self.assertEqual(page_url, redirected_url)
        self.assertEqual(response.final_url, redirected_url)

    async def test_good_bundle_preserves_authoritative_row_scores(self) -> None:
        bundle = await self._bundle()
        freshness = FeedFreshnessResult(True, ["recent update surface"], [], [])
        trust = TrustSurfaceResult(
            found={"about": ["sitemap"], "contact": ["homepage"], "privacy": ["homepage"]},
            positives=["core trust surfaces are discoverable"],
            issues=[],
            caveats=[],
        )
        with (
            patch(
                "app.checks.search_discovery._freshness_surface",
                new=AsyncMock(return_value=freshness),
            ),
            patch("app.checks.search_discovery._trust_surface_discovery", return_value=trust),
        ):
            search = await check_search_discovery(self.context, False, bundle)

        llms = await check_llms_txt(self.context, bundle)
        schema = await check_schema_ld(self.context, False, bundle)
        ssr = await check_ssr(self.context, bundle)

        self.assertEqual((search.state, search.score), ("pass", 4))
        self.assertEqual((llms.state, llms.score), ("pass", 5))
        self.assertEqual((schema.state, schema.score), ("pass", 5))
        self.assertEqual((ssr.state, ssr.score), ("pass", 4))
        self.assertIn("no Firecrawl or other extraction-provider API was called", llms.finding)

    async def test_blurb_issue_only_caps_existing_sampled_metadata_point(self) -> None:
        bundle = await self._bundle()
        degraded = replace(
            bundle,
            search_blurb=SearchBlurbAnalysis(
                pages=bundle.search_blurb.pages,
                positives=(),
                issues=("2 sampled page(s) reuse a normalized title across 1 duplicate group(s).",),
                caveats=(),
            ),
        )
        freshness = FeedFreshnessResult(True, ["recent update surface"], [], [])
        trust = TrustSurfaceResult({}, [], [], [])
        with (
            patch(
                "app.checks.search_discovery._freshness_surface",
                new=AsyncMock(return_value=freshness),
            ),
            patch("app.checks.search_discovery._trust_surface_discovery", return_value=trust),
        ):
            result = await check_search_discovery(self.context, False, degraded)

        self.assertEqual((result.state, result.score), ("partial", 3))
        self.assertIn("search-blurb proxy", result.finding)
        self.assertIn("actual DuckDuckGo and Bing snippet display was not verified", result.finding)

        already_degraded = SamplePageMetadataResult(
            False, [], ["sampled metadata is incomplete"], []
        )
        with (
            patch(
                "app.checks.search_discovery._freshness_surface",
                new=AsyncMock(return_value=freshness),
            ),
            patch("app.checks.search_discovery._trust_surface_discovery", return_value=trust),
            patch(
                "app.checks.search_discovery._sample_pages_have_discovery_metadata",
                new=AsyncMock(return_value=already_degraded),
            ),
        ):
            result = await check_search_discovery(self.context, False, degraded)
        self.assertEqual(result.score, 3)

    async def test_hidden_app_shell_caps_only_an_otherwise_passing_ssr_row(self) -> None:
        bundle = await self._bundle()
        hidden_words = " ".join(f"important content {index}" for index in range(100))
        html = (
            '<html><body><main hidden><h1>Hidden</h1><p>'
            + hidden_words
            + '</p></main><div id="app"></div>'
            + "".join(f'<script src="/{index}.js"></script>' for index in range(4))
            + "</body></html>"
        )
        hidden_context = make_audit_context(homepage_html=html)
        extraction = analyse_extraction_readiness(
            ExtractionReadinessInput(url="https://example.com", raw_html=html)
        )
        result = await check_ssr(
            hidden_context,
            replace(bundle, extraction_readiness=extraction),
        )

        self.assertEqual((result.state, result.score), ("partial", 3))
        self.assertIn("hidden source node", result.finding)

    async def test_scope_off_excludes_commerce_coherence_and_contract_is_fixed(self) -> None:
        bundle = await self._bundle(include_ecommerce=False)
        schema = await check_schema_ld(self.context, False, bundle)

        self.assertEqual(bundle.extraction_readiness.commerce_missing_fields, ())
        self.assertEqual(bundle.extraction_readiness.commerce_schema_visible_mismatches, ())
        self.assertNotIn("Visible product content", schema.finding)
        self.assertEqual(ESSENTIALS_CHECK_GROUP_COUNT, 13)
        self.assertEqual(ESSENTIALS_CHECKED_MAX, 56)


if __name__ == "__main__":
    unittest.main()
