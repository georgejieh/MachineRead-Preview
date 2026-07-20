import json
import sys
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.checks.extraction_readiness import (
    ExtractionReadinessAnalysis,
    ExtractionReadinessInput,
    analyse_extraction_readiness,
)
from app.fetching import FetchResult


def _response(
    text: str,
    *,
    content_type: str = "text/markdown",
    status_code: int = 200,
) -> FetchResult:
    return FetchResult(
        requested_url="https://example.com/index.md",
        final_url="https://example.com/index.md",
        status_code=status_code,
        headers={"content-type": content_type},
        text=text,
        elapsed_ms=1,
        redirect_chain=[],
    )


def _good_html(schema: str = "") -> str:
    main_text = " ".join(f"useful extraction word {index}" for index in range(90))
    return f"""
    <html><head><title>Extraction guide</title>{schema}</head><body>
    <header>MachineRead</header><nav>Home Guide</nav>
    <main><h1>Extraction guide</h1><p>{main_text}</p></main>
    <footer>Contact</footer></body></html>
    """


def _complete_product_schema(*, include_variant: bool = True) -> str:
    variant = (
        '"hasVariant": [{"@type": "Product", "name": "Trail shoe, blue"}],'
        if include_variant
        else ""
    )
    return """
    <script type="application/ld+json">{
      "@context": "https://schema.org",
      "@type": "Product",
      "name": "Trail shoe",
      "description": "A durable trail shoe",
      "image": "https://example.com/shoe.jpg",
      "brand": {"@type": "Brand", "name": "Example"},
      "sku": "SHOE-1",
      "gtin13": "1234567890123",
      __VARIANT__
      "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.8"},
      "offers": {
        "@type": "Offer",
        "price": "99.00",
        "priceCurrency": "USD",
        "availability": "https://schema.org/InStock",
        "priceValidUntil": "2027-01-01",
        "shippingDetails": {"@type": "OfferShippingDetails"},
        "hasMerchantReturnPolicy": {"@type": "MerchantReturnPolicy"}
      }
    }</script>
    """.replace("__VARIANT__", variant)


def _complete_visible_product_html(
    schema: str,
    *,
    price: str = "99.00",
    include_variant: bool = True,
) -> str:
    details = " ".join(f"durable product detail {index}" for index in range(90))
    variant = (
        '<select name="size-variant"><option>8</option><option>9</option></select>'
        if include_variant
        else '<label>Size <input name="size" value="9"></label>'
    )
    return f"""
    <html><head><title>Trail shoe</title>{schema}</head><body><main>
    <h1>Trail shoe</h1>
    <img src="/shoe.jpg" alt="Trail shoe">
    <p>{details}</p>
    <p><span data-price="{price}">${price}</span> <span>In stock</span></p>
    <p>SKU: SHOE-1</p>
    {variant}
    <p>Free shipping and delivery. Returns and refunds are available.</p>
    </main></body></html>
    """


class ExtractionReadinessTests(unittest.TestCase):
    def test_reports_good_local_source_metrics(self) -> None:
        markdown = "# Extraction guide\n\n" + "Useful Markdown text for agents. " * 20
        result = analyse_extraction_readiness(
            ExtractionReadinessInput(
                url="https://example.com",
                raw_html=_good_html(),
                markdown_responses=[_response(markdown)],
                llms_txt="# Example\n\n- [Guide](https://example.com/guide)",
                sitemap_xml=(
                    "<urlset><url><loc>https://example.com/</loc>"
                    "<lastmod>2026-07-01</lastmod></url></urlset>"
                ),
            )
        )

        self.assertEqual(result.rendering_state, "readable")
        self.assertGreaterEqual(result.main_content_words, 80)
        self.assertGreater(result.cleaned_text_bytes, 0)
        self.assertLess(result.boilerplate_ratio, 0.1)
        self.assertLess(result.navigation_ratio, 0.1)
        self.assertFalse(result.app_shell_risk)
        self.assertEqual(result.markdown_alternates_checked, 1)
        self.assertEqual(result.markdown_usable_count, 1)
        self.assertTrue(result.llms_txt_valid)
        self.assertTrue(result.sitemap_valid)
        self.assertEqual(result.sitemap_url_count, 1)
        self.assertTrue(result.sitemap_has_lastmod)

    def test_flags_boilerplate_heavy_html(self) -> None:
        navigation = " ".join(f"navigation{index}" for index in range(180))
        html = f"<html><body><nav>{navigation}</nav><main>Small useful article.</main></body></html>"

        result = analyse_extraction_readiness(
            ExtractionReadinessInput("https://example.com", html)
        )

        self.assertGreater(result.boilerplate_ratio, 0.65)
        self.assertGreater(result.navigation_ratio, 0.45)
        self.assertTrue(any("boilerplate" in issue for issue in result.issues))
        self.assertTrue(any("navigation-heavy" in issue for issue in result.issues))

    def test_detects_probable_app_shell(self) -> None:
        html = "<html><body><div id='__next'>Loading</div><script src='/app.js'></script></body></html>"

        result = analyse_extraction_readiness(
            ExtractionReadinessInput("https://example.com", html)
        )

        self.assertEqual(result.rendering_state, "probable_js_shell")
        self.assertTrue(result.app_shell_signals_present)
        self.assertTrue(result.app_shell_risk)
        self.assertTrue(any("app-shell risk" in issue for issue in result.issues))

    def test_excludes_conservatively_hidden_main_content_from_metrics(self) -> None:
        hidden_words = " ".join(f"hiddenword{index}" for index in range(120))
        html = f"<html><body><main aria-hidden='true'>{hidden_words}</main><p>Loading</p></body></html>"

        result = analyse_extraction_readiness(
            ExtractionReadinessInput("https://example.com", html)
        )

        self.assertEqual(result.hidden_node_count, 1)
        self.assertNotEqual(result.rendering_state, "readable")
        self.assertLess(result.cleaned_text_words, 5)
        self.assertTrue(any("External CSS" in caveat for caveat in result.caveats))

    def test_accepts_and_rejects_already_fetched_markdown(self) -> None:
        usable = "# Guide\n\n" + "Useful Markdown content with enough words for extraction. " * 12
        html_response = _response(
            "<html><body>Not Markdown</body></html>",
            content_type="text/html",
        )

        result = analyse_extraction_readiness(
            ExtractionReadinessInput(
                "https://example.com",
                _good_html(),
                markdown_responses=(_response(usable), html_response),
            )
        )

        self.assertEqual(result.markdown_alternates_supplied, 2)
        self.assertEqual(result.markdown_alternates_checked, 2)
        self.assertEqual(result.markdown_usable_count, 1)
        self.assertGreater(result.best_markdown_bytes, 0)
        self.assertGreater(result.best_markdown_words, 0)
        self.assertNotEqual(result.best_markdown_word_delta, 0)
        self.assertGreater(result.best_markdown_to_raw_byte_ratio, 0)
        self.assertGreater(result.best_markdown_to_main_word_ratio, 0)

    def test_caps_best_markdown_comparison_before_counting(self) -> None:
        oversized_markdown = "# Guide\n\n" + ("boundedword " * 120_000)

        result = analyse_extraction_readiness(
            ExtractionReadinessInput(
                "https://example.com",
                _good_html(),
                markdown_responses=(_response(oversized_markdown),),
            )
        )

        self.assertEqual(result.markdown_usable_count, 1)
        self.assertLessEqual(result.best_markdown_bytes, 1024 * 1024)
        self.assertGreater(result.best_markdown_token_coverage_ratio, 0)
        self.assertTrue(any("Markdown alternate 1 exceeded 1 MiB" in item for item in result.caveats))

    def test_equal_length_unrelated_markdown_is_not_praised_as_coverage(self) -> None:
        unrelated = "# Astronomy\n\n" + (
            "galaxy telescope nebula observatory planet starlight comet orbit " * 50
        )

        result = analyse_extraction_readiness(
            ExtractionReadinessInput(
                "https://example.com",
                _good_html(),
                markdown_responses=(_response(unrelated),),
            )
        )

        self.assertLess(result.best_markdown_token_coverage_ratio, 0.5)
        self.assertFalse(any("overlaps at least 80%" in item for item in result.positives))
        self.assertTrue(any("overlaps less than half" in item for item in result.issues))

    def test_shorter_relevant_markdown_beats_longer_unrelated_alternate(self) -> None:
        unrelated = "# Astronomy\n\n" + (
            "galaxy telescope nebula observatory planet starlight comet orbit " * 80
        )
        relevant = (
            "# Extraction guide\n\nUseful extraction word "
            + " ".join(str(index) for index in range(90))
        )

        result = analyse_extraction_readiness(
            ExtractionReadinessInput(
                "https://example.com",
                _good_html(),
                markdown_responses=(_response(unrelated), _response(relevant)),
            )
        )

        self.assertEqual(result.markdown_usable_count, 2)
        self.assertLess(result.best_markdown_words, len(unrelated.split()))
        self.assertGreaterEqual(result.best_markdown_token_coverage_ratio, 0.8)
        self.assertEqual(result.best_markdown_bytes, len(relevant.encode("utf-8")))
        self.assertFalse(any("overlaps less than half" in item for item in result.issues))

    def test_distinguishes_llms_missing_empty_invalid_and_valid(self) -> None:
        cases = (
            (None, False, False, False),
            ("", True, False, False),
            ("not a valid llms file because it has no title or link", True, True, False),
            ("# Example\n\n[Guide](https://example.com/guide)", True, True, True),
        )
        for content, supplied, present, valid in cases:
            with self.subTest(content=content):
                result = analyse_extraction_readiness(
                    ExtractionReadinessInput("https://example.com", _good_html(), llms_txt=content)
                )
                self.assertEqual(result.llms_txt_supplied, supplied)
                self.assertEqual(result.llms_txt_present, present)
                self.assertEqual(result.llms_txt_valid, valid)

    def test_distinguishes_sitemap_shapes_missing_empty_and_malformed(self) -> None:
        cases = (
            (None, False, False, False, 0, False, False),
            ("", True, False, False, 0, False, False),
            ("<urlset>", True, True, False, 0, False, False),
            (
                "<urlset><url><loc>https://example.com/</loc><lastmod>2026-07-01</lastmod></url></urlset>",
                True,
                True,
                True,
                1,
                True,
                False,
            ),
            (
                "<sitemapindex><sitemap><loc>https://example.com/products.xml</loc></sitemap></sitemapindex>",
                True,
                True,
                True,
                1,
                False,
                True,
            ),
        )
        for content, supplied, present, valid, count, lastmod, is_index in cases:
            with self.subTest(content=content):
                result = analyse_extraction_readiness(
                    ExtractionReadinessInput(
                        "https://example.com", _good_html(), sitemap_xml=content
                    )
                )
                self.assertEqual(result.sitemap_xml_supplied, supplied)
                self.assertEqual(result.sitemap_xml_present, present)
                self.assertEqual(result.sitemap_valid, valid)
                self.assertEqual(result.sitemap_url_count, count)
                self.assertEqual(result.sitemap_has_lastmod, lastmod)
                self.assertEqual(result.sitemap_is_index, is_index)

    def test_collects_graph_nested_and_multiple_schema_types(self) -> None:
        schema = """
        <script type="application/ld+json">{
          "@context": "https://schema.org",
          "@graph": [
            {"@type": ["Organization", "Thing"], "name": "Example"},
            {"@type": "Product", "name": "Shoe", "offers": {"@type": "Offer", "price": "10"}}
          ]
        }</script>
        <script type="application/ld+json">{"@type": "WebSite", "name": "Example"}</script>
        <script type="application/ld+json">{invalid}</script>
        """

        result = analyse_extraction_readiness(
            ExtractionReadinessInput("https://example.com", _good_html(schema))
        )

        self.assertEqual(
            result.schema_types,
            ("Organization", "Thing", "Product", "Offer", "WebSite"),
        )
        self.assertEqual(result.invalid_schema_count, 1)
        self.assertTrue(result.product_schema_present)
        self.assertTrue(result.offer_schema_present)

    def test_deep_and_malformed_json_ld_are_bounded_without_crashing(self) -> None:
        # Malformed input: object-open followed by array-close with no
        # matching object-close. Deep-but-valid JSON is exercised by the
        # nested case below via the schema-traversal depth cap.
        malformed_json = "{" + "]" * 50
        malformed = f'<script type="application/ld+json">{malformed_json}</script>'
        malformed_result = analyse_extraction_readiness(
            ExtractionReadinessInput("https://example.com", _good_html(malformed))
        )
        self.assertGreaterEqual(malformed_result.invalid_schema_count, 1)

        nested = '{"@type":"Product"}'
        for _ in range(40):
            nested = '{"child":' + nested + "}"
        nested_html = f'<script type="application/ld+json">{nested}</script>'
        nested_result = analyse_extraction_readiness(
            ExtractionReadinessInput("https://example.com", _good_html(nested_html))
        )
        self.assertTrue(nested_result.schema_traversal_truncated)
        self.assertTrue(any("safety cap" in item for item in nested_result.caveats))

    def test_json_ld_value_error_is_counted_as_invalid(self) -> None:
        oversized_integer = "9" * 5000
        schema = (
            '<script type="application/ld+json">'
            '{"@type":"Product","sku":'
            + oversized_integer
            + "}</script>"
        )

        result = analyse_extraction_readiness(
            ExtractionReadinessInput("https://example.com", _good_html(schema))
        )

        self.assertEqual(result.invalid_schema_count, 1)
        self.assertTrue(any("JSON-LD script block" in item for item in result.issues))

    def test_schema_types_are_sanitised_and_count_capped(self) -> None:
        sentinel = "PRIVATE-SOURCE-CONTENT-SENTINEL"
        nodes = [{"@type": sentinel}] + [{"@type": f"Type{index}"} for index in range(40)]
        schema = '<script type="application/ld+json">' + json.dumps(nodes) + "</script>"

        result = analyse_extraction_readiness(
            ExtractionReadinessInput("https://example.com", _good_html(schema))
        )

        self.assertLessEqual(len(result.schema_types), 32)
        self.assertNotIn(sentinel, result.schema_types)
        self.assertNotIn(sentinel, repr(result))
        self.assertGreater(result.schema_types_ignored, 0)
        self.assertTrue(result.schema_traversal_truncated)

    def test_commerce_fields_are_complete_incomplete_or_out_of_scope(self) -> None:
        complete = analyse_extraction_readiness(
            ExtractionReadinessInput(
                "https://example.com",
                _complete_visible_product_html(_complete_product_schema()),
                include_ecommerce=True,
            )
        )
        incomplete_schema = """
        <script type="application/ld+json">{
          "@type": "Product", "name": "Shoe", "offers": {"@type": "Offer", "price": "10"}
        }</script>
        """
        incomplete = analyse_extraction_readiness(
            ExtractionReadinessInput(
                "https://example.com",
                _good_html(incomplete_schema),
                include_ecommerce=True,
            )
        )
        off = analyse_extraction_readiness(
            ExtractionReadinessInput("https://example.com", _good_html(incomplete_schema))
        )

        self.assertEqual(complete.commerce_missing_fields, ())
        self.assertEqual(complete.commerce_visible_missing_fields, ())
        self.assertEqual(complete.commerce_schema_visible_mismatches, ())
        self.assertIn("variants", complete.commerce_visible_fields)
        self.assertTrue(complete.product_schema_present)
        self.assertTrue(complete.offer_schema_present)
        self.assertIn("description", incomplete.commerce_missing_fields)
        self.assertIn("offers.priceCurrency", incomplete.commerce_missing_fields)
        self.assertNotIn("variants", incomplete.commerce_visible_missing_fields)
        self.assertEqual(off.commerce_missing_fields, ())
        self.assertEqual(off.commerce_visible_missing_fields, ())
        self.assertFalse(any("Commerce schema" in issue for issue in off.issues))

    def test_reports_visible_schema_conflicts_without_retaining_values(self) -> None:
        result = analyse_extraction_readiness(
            ExtractionReadinessInput(
                "https://example.com",
                _complete_visible_product_html(_complete_product_schema(), price="129.00"),
                include_ecommerce=True,
            )
        )

        self.assertIn("price", result.commerce_schema_visible_mismatches)
        self.assertNotIn("129.00", repr(result))
        self.assertNotIn("99.00", repr(result))

    def test_variants_are_optional_but_asymmetric_cues_are_reported(self) -> None:
        neither = analyse_extraction_readiness(
            ExtractionReadinessInput(
                "https://example.com",
                _complete_visible_product_html(
                    _complete_product_schema(include_variant=False),
                    include_variant=False,
                ),
                include_ecommerce=True,
            )
        )
        visible_only = analyse_extraction_readiness(
            ExtractionReadinessInput(
                "https://example.com",
                _complete_visible_product_html(
                    _complete_product_schema(include_variant=False),
                    include_variant=True,
                ),
                include_ecommerce=True,
            )
        )
        schema_only = analyse_extraction_readiness(
            ExtractionReadinessInput(
                "https://example.com",
                _complete_visible_product_html(
                    _complete_product_schema(include_variant=True),
                    include_variant=False,
                ),
                include_ecommerce=True,
            )
        )

        self.assertNotIn("variants", neither.commerce_missing_fields)
        self.assertNotIn("variants", neither.commerce_visible_missing_fields)
        self.assertNotIn("variants", neither.commerce_schema_visible_mismatches)
        self.assertIn("variants", visible_only.commerce_schema_visible_mismatches)
        self.assertIn("variants", schema_only.commerce_schema_visible_mismatches)

    def test_grouped_price_formats_match_equivalent_schema_price(self) -> None:
        for visible_price in ("1,299.00", "1299.00", "1.299,00"):
            schema = _complete_product_schema().replace('"price": "99.00"', '"price": "1299.00"')
            with self.subTest(visible_price=visible_price):
                result = analyse_extraction_readiness(
                    ExtractionReadinessInput(
                        "https://example.com",
                        _complete_visible_product_html(schema, price=visible_price),
                        include_ecommerce=True,
                    )
                )
                self.assertNotIn("price", result.commerce_schema_visible_mismatches)

    def test_handles_empty_and_malformed_html_without_raising(self) -> None:
        for html in ("", "<html><script type='application/ld+json'>{broken</script><main>"):
            with self.subTest(html=html):
                result = analyse_extraction_readiness(
                    ExtractionReadinessInput("https://example.com", html)
                )
                self.assertIsInstance(result, ExtractionReadinessAnalysis)
                self.assertTrue(result.issues)

    def test_caps_sources_and_ignores_excess_markdown_alternates(self) -> None:
        oversized_html = "x" * ((15 * 1024 * 1024) + 1)
        oversized_llms = "# Example\nhttps://example.com\n" + ("x" * (512 * 1024))
        oversized_sitemap = "<urlset>" + (" " * (1024 * 1024))
        responses = [_response("# Guide\n\n" + "useful words " * 40) for _ in range(6)]

        result = analyse_extraction_readiness(
            ExtractionReadinessInput(
                "https://example.com",
                oversized_html,
                markdown_responses=responses,
                llms_txt=oversized_llms,
                sitemap_xml=oversized_sitemap,
            )
        )

        self.assertTrue(result.raw_html_truncated)
        self.assertEqual(result.markdown_alternates_supplied, 6)
        self.assertEqual(result.markdown_alternates_checked, 5)
        self.assertTrue(any("15 MiB" in caveat for caveat in result.caveats))
        self.assertTrue(any("512 KiB" in caveat for caveat in result.caveats))
        self.assertTrue(any("Sitemap XML exceeded 1 MiB" in caveat for caveat in result.caveats))
        self.assertTrue(any("1 were ignored" in caveat for caveat in result.caveats))

    def test_is_pure_frozen_and_retains_no_source_content(self) -> None:
        sentinel = "PRIVATE-SOURCE-CONTENT-SENTINEL"
        profile_input = ExtractionReadinessInput(
            "https://example.com",
            f"<html><main>{sentinel}</main></html>",
        )
        with patch("app.fetching.fetch_url") as fetch_url:
            result = analyse_extraction_readiness(profile_input)

        fetch_url.assert_not_called()
        with self.assertRaises(FrozenInstanceError):
            result.cleaned_text_words = 2  # type: ignore[misc]
        output_field_names = {field.name for field in fields(result)}
        self.assertFalse({"url", "raw_html", "cleaned_text", "main_text"} & output_field_names)
        self.assertNotIn(sentinel, repr(result))
        self.assertTrue(any("local source-response readiness" in item for item in result.caveats))
        self.assertTrue(any("no Firecrawl" in item for item in result.caveats))
        self.assertTrue(any("Browser-rendered output" in item for item in result.caveats))
        self.assertTrue(any("Actual product extraction" in item for item in result.caveats))


if __name__ == "__main__":
    unittest.main()
