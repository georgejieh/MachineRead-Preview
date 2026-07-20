import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.checks.search_blurb import BlurbPageInput, analyse_search_blurbs, extract_page_blurb


def _html(
    description: str,
    *,
    canonical: str = "/guide",
    social: str | None = None,
    title: str = "Trail running shoe guide",
    visible_suffix: str = "",
) -> str:
    social_tags = "" if social is None else (
        f'<meta property="og:description" content="{social}">'
        f'<meta name="twitter:description" content="{social}">'
    )
    return f"""
    <html><head><title>{title}</title>
    <meta name="description" content="{description}">
    <link rel="canonical alternate" href="{canonical}">{social_tags}</head>
    <body><nav>Cookie settings and store links</nav><main>
    <h1>How to choose trail running shoes</h1>
    <p>Learn how grip, cushioning, fit, terrain, and weather affect your choice of trail running shoes.</p>
    <p>This practical guide compares durable footwear for rocky and muddy routes. {visible_suffix}</p>
    </main><footer>All rights reserved</footer></body></html>
    """


class SearchBlurbTests(unittest.TestCase):
    def test_extracts_owned_signals_and_cleans_main_text(self) -> None:
        description = "Learn how grip, cushioning, fit, and terrain shape your choice of trail running shoes."
        signals = extract_page_blurb(BlurbPageInput("https://example.com/start", _html(description)))

        self.assertEqual(signals.title, "Trail running shoe guide")
        self.assertEqual(signals.h1, "How to choose trail running shoes")
        self.assertEqual(signals.meta_description, description)
        self.assertEqual(signals.canonical_url, "https://example.com/guide")
        self.assertNotIn("Cookie settings", signals.main_text)
        self.assertNotIn("All rights reserved", signals.main_text)
        with self.assertRaises(FrozenInstanceError):
            signals.title = "changed"  # type: ignore[misc]

    def test_reports_complete_coherent_metadata_and_required_caveat(self) -> None:
        description = "Learn how grip, cushioning, fit, and terrain shape your choice of trail running shoes."
        result = analyse_search_blurbs(
            [BlurbPageInput("https://example.com/guide", _html(description, social=description))]
        )

        self.assertFalse(result.issues)
        self.assertTrue(any("align with visible page content" in item for item in result.positives))
        self.assertTrue(any("Open Graph/Twitter" in item for item in result.positives))
        self.assertIn("actual DuckDuckGo or Bing snippet selection and display were not verified", result.caveats[0])

    def test_reports_missing_fields_and_keeps_canonical_absent(self) -> None:
        result = analyse_search_blurbs([BlurbPageInput("https://example.com", "<html><body></body></html>")])

        self.assertIsNone(result.pages[0].canonical_url)
        self.assertTrue(any("missing title" in item for item in result.issues))
        self.assertTrue(any("missing meta description" in item for item in result.issues))
        self.assertTrue(any("missing H1" in item for item in result.issues))
        self.assertTrue(any("missing extractable main text" in item for item in result.issues))
        self.assertTrue(any("missing canonical URL" in item for item in result.issues))

    def test_flags_conservative_length_bounds(self) -> None:
        pages = [
            BlurbPageInput("https://example.com/short", _html("Too short.")),
            BlurbPageInput("https://example.com/long", _html("Useful trail running details " * 12)),
        ]
        result = analyse_search_blurbs(pages)

        self.assertTrue(any("shorter than 50" in item for item in result.issues))
        self.assertTrue(any("longer than 200" in item for item in result.issues))

    def test_flags_normalized_duplicates_across_pages(self) -> None:
        first = "Choose durable trail shoes for rocky routes, wet weather, and comfortable long-distance running."
        second = "  CHOOSE durable trail shoes for rocky routes wet weather and comfortable long distance running!!! "
        result = analyse_search_blurbs([
            BlurbPageInput("https://example.com/a", _html(first, canonical="/a")),
            BlurbPageInput("https://example.com/b", _html(second, canonical="/b")),
        ])

        self.assertTrue(any("2 sampled page(s) reuse a normalized meta description" in item for item in result.issues))

    def test_flags_normalized_duplicate_titles_across_pages(self) -> None:
        description_a = "Choose durable trail shoes for rocky routes, wet weather, and comfortable running."
        description_b = "Compare trail footwear cushioning, grip, fit, and protection for long routes."
        result = analyse_search_blurbs([
            BlurbPageInput(
                "https://example.com/a",
                _html(description_a, canonical="/a", title="Trail-running Shoe Guide!"),
            ),
            BlurbPageInput(
                "https://example.com/b",
                _html(description_b, canonical="/b", title=" trail running shoe guide "),
            ),
        ])

        self.assertTrue(any("2 sampled page(s) reuse a normalized title" in item for item in result.issues))

    def test_flags_page_owned_stale_year_conflict_without_live_claim(self) -> None:
        description = "Read the 2024 trail running shoe guide for grip, cushioning, fit, and terrain advice."
        result = analyse_search_blurbs([
            BlurbPageInput(
                "https://example.com/guide",
                _html(description, canonical="/guide", visible_suffix="Updated for 2026."),
            )
        ])

        self.assertTrue(any("older year in page-owned description metadata" in item for item in result.issues))
        self.assertFalse(any("snippet is stale" in item.casefold() for item in result.issues))

    def test_does_not_flag_year_without_newer_visible_year(self) -> None:
        description = "Read the 2024 trail running shoe guide for grip, cushioning, fit, and terrain advice."
        result = analyse_search_blurbs([
            BlurbPageInput("https://example.com/guide", _html(description, canonical="/guide"))
        ])

        self.assertFalse(any("older year" in item for item in result.issues))

    def test_flags_off_host_and_different_canonical_targets(self) -> None:
        description = "Learn how grip, cushioning, fit, and terrain shape your choice of trail running shoes."
        result = analyse_search_blurbs([
            BlurbPageInput("https://example.com/a", _html(description, canonical="https://other.test/a")),
            BlurbPageInput("https://example.com/b", _html(description, canonical="/other")),
        ])

        self.assertTrue(any("off-host canonical" in item for item in result.issues))
        self.assertTrue(any("canonicalize to a different same-host URL" in item for item in result.issues))

    def test_flags_shared_canonical_collision_but_accepts_normalized_self_canonicals(self) -> None:
        description = "Learn how grip, cushioning, fit, and terrain shape your choice of trail running shoes."
        collisions = analyse_search_blurbs([
            BlurbPageInput("https://example.com/a", _html(description, canonical="/shared")),
            BlurbPageInput("https://example.com/b", _html(description, canonical="/shared")),
        ])
        self.assertTrue(any("share a canonical target" in item for item in collisions.issues))

        self_canonical = analyse_search_blurbs([
            BlurbPageInput(
                "https://EXAMPLE.com:443/guide/?source=test#top",
                _html(description, canonical="https://example.com/guide"),
            )
        ])
        self.assertFalse(any("canonicalize" in item for item in self_canonical.issues))
        self.assertTrue(any("normalized self-canonicals" in item for item in self_canonical.positives))

    def test_malformed_canonical_is_reported_without_crashing(self) -> None:
        description = "Learn how grip, cushioning, fit, and terrain shape your choice of trail running shoes."
        result = analyse_search_blurbs([
            BlurbPageInput(
                "https://example.com/guide",
                _html(description, canonical="https://example.com:bad/path"),
            ),
            BlurbPageInput(
                "https://example.com/other",
                _html(description, canonical="https://[broken/path"),
            ),
        ])

        self.assertTrue(any("2 sampled page(s) publish a malformed canonical URL" in item for item in result.issues))
        self.assertFalse(any("normalized self-canonicals" in item for item in result.positives))

    def test_missing_social_descriptions_are_optional_caveats(self) -> None:
        description = "Learn how grip, cushioning, fit, and terrain shape your choice of trail running shoes."
        result = analyse_search_blurbs([
            BlurbPageInput("https://example.com/guide", _html(description, canonical="/guide"))
        ])

        self.assertTrue(any("optional Open Graph description" in item for item in result.caveats))
        self.assertTrue(any("optional Twitter description" in item for item in result.caveats))
        self.assertFalse(any("Open Graph" in item for item in result.issues))

    def test_flags_placeholder_and_boilerplate_descriptions(self) -> None:
        result = analyse_search_blurbs([
            BlurbPageInput(
                "https://example.com/a",
                _html("Lorem ipsum description goes here for this future page and its unfinished content."),
            ),
            BlurbPageInput(
                "https://example.com/b",
                _html("We use cookies. Privacy policy and terms of service. All rights reserved on this site."),
            ),
        ])

        self.assertTrue(
            any("2 sampled meta description(s) look like placeholder or boilerplate" in item for item in result.issues)
        )

    def test_flags_low_overlap_only_when_token_guards_are_met(self) -> None:
        unrelated = (
            "Explore astronomy telescopes, distant galaxies, planetary imaging, "
            "and observatory equipment tonight."
        )
        guarded_short = "Astronomy telescope guide"
        result = analyse_search_blurbs([
            BlurbPageInput("https://example.com/unrelated", _html(unrelated, canonical="/unrelated")),
            BlurbPageInput("https://example.com/short", _html(guarded_short, canonical="/short")),
        ])

        self.assertTrue(any("1 sampled meta description(s) have low token overlap" in item for item in result.issues))

    def test_flags_material_social_description_mismatch(self) -> None:
        description = "Learn how grip, cushioning, fit, and terrain shape your choice of trail running shoes."
        unrelated = "Astronomy telescopes reveal galaxies, planets, nebulae, observatories, and night skies."
        result = analyse_search_blurbs([
            BlurbPageInput("https://example.com/guide", _html(description, social=unrelated))
        ])

        self.assertTrue(
            any("2 Open Graph/Twitter description(s) materially mismatch" in item for item in result.issues)
        )


if __name__ == "__main__":
    unittest.main()
