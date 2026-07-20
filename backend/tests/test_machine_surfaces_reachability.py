import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.audit_context import AuditContext
from app.checks.machine_surfaces import _surface_reachability, check_machine_surfaces
from app.fetching import FetchResult


_BASE_URL = "https://example.com"


def _fetch_result(
    url: str,
    status_code: int | None = 200,
    text: str = "",
    headers: dict[str, str] | None = None,
    error: str | None = None,
) -> FetchResult:
    return FetchResult(
        requested_url=url,
        final_url=url,
        status_code=status_code,
        headers=headers or {},
        text=text,
        elapsed_ms=1,
        redirect_chain=[],
        error=error,
    )


def _context(
    homepage_html: str,
    homepage_headers: dict[str, str] | None = None,
    homepage_ok: bool = True,
) -> AuditContext:
    homepage = _fetch_result(
        _BASE_URL,
        status_code=200 if homepage_ok else None,
        text=homepage_html,
        headers=homepage_headers,
        error=None if homepage_ok else "homepage unavailable",
    )
    return AuditContext(
        url=_BASE_URL,
        homepage=homepage,
        robots=_fetch_result(f"{_BASE_URL}/robots.txt", status_code=404, text="not found"),
        sitemap=_fetch_result(f"{_BASE_URL}/sitemap.xml", status_code=404, text="not found"),
    )


async def _surface_fetch(url: str, **kwargs: object) -> FetchResult:
    if url.endswith("/llms-full.txt"):
        return _fetch_result(
            url,
            text="# Example full export\n\nMachine-readable documentation for agents.",
            headers={"content-type": "text/plain"},
        )
    return _fetch_result(url, status_code=404, text="not found")


class MachineSurfaceReachabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_linked_surface_has_no_orphan_note(self) -> None:
        context = _context(
            '<html><body><a href="https://example.com/llms-full.txt">LLM export</a></body></html>'
        )

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=_surface_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            result = await check_machine_surfaces(context)

        self.assertNotIn("Reachability note:", result.finding)
        self.assertNotIn("agents may not discover them", result.finding)

    async def test_unlinked_surface_has_orphan_note(self) -> None:
        context = _context("<html><body><main>Example</main></body></html>")

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=_surface_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            result = await check_machine_surfaces(context)

        self.assertIn("Reachability note: /llms-full.txt", result.finding)
        self.assertIn("agents may not discover them", result.finding)

    async def test_link_header_makes_surface_reachable(self) -> None:
        context = _context(
            "<html><body><main>Example</main></body></html>",
            homepage_headers={
                "link": '<https://example.com/llms-full.txt>; rel="alternate"; type="text/plain"'
            },
        )

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=_surface_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            result = await check_machine_surfaces(context)

        self.assertNotIn("Reachability note:", result.finding)

    async def test_multiple_orphans_are_all_listed(self) -> None:
        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            if url.endswith("/llms-full.txt"):
                return await _surface_fetch(url, **kwargs)
            if url.endswith("/feed.xml"):
                return _fetch_result(
                    url,
                    text="<rss><channel><title>Example updates</title></channel></rss>",
                    headers={"content-type": "application/rss+xml"},
                )
            return _fetch_result(url, status_code=404, text="not found")

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=fake_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            result = await check_machine_surfaces(
                _context("<html><body><main>Example</main></body></html>")
            )

        self.assertIn("Reachability note:", result.finding)
        self.assertIn("/feed.xml", result.finding)
        self.assertIn("/llms-full.txt", result.finding)

    async def test_no_discovered_surfaces_has_no_orphan_note(self) -> None:
        async def missing_fetch(url: str, **kwargs: object) -> FetchResult:
            return _fetch_result(url, status_code=404, text="not found")

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=missing_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            result = await check_machine_surfaces(
                _context("<html><body><main>Example</main></body></html>")
            )

        self.assertNotIn("Reachability note:", result.finding)

    async def test_orphan_note_changes_only_finding(self) -> None:
        linked_context = _context(
            '<html><body><link href="/llms-full.txt" rel="alternate"></body></html>'
        )
        orphaned_context = _context("<html><body><main>Example</main></body></html>")

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=_surface_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            linked_result = await check_machine_surfaces(linked_context)
            orphaned_result = await check_machine_surfaces(orphaned_context)

        self.assertEqual(orphaned_result.state, linked_result.state)
        self.assertEqual(orphaned_result.score, linked_result.score)
        self.assertEqual(orphaned_result.fix, linked_result.fix)
        self.assertNotEqual(orphaned_result.finding, linked_result.finding)
        self.assertIn("Reachability note:", orphaned_result.finding)
        self.assertNotIn("Reachability note:", orphaned_result.fix)

    async def test_relative_homepage_href_resolves_to_surface(self) -> None:
        context = _context(
            '<html><body><a href="llms-full.txt">LLM export</a></body></html>'
        )

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=_surface_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            result = await check_machine_surfaces(context)

        self.assertNotIn("Reachability note:", result.finding)

    def test_missing_homepage_soup_returns_empty_note(self) -> None:
        context = _context("", homepage_ok=False)

        note = _surface_reachability(
            {"/llms-full.txt"},
            None,
            "",
            context,
        )

        self.assertEqual(note, "")

    # ------------------------------------------------------------------
    # Conventional path probes (Part A)
    # ------------------------------------------------------------------

    async def test_conventional_docs_path_appears_positively(self) -> None:
        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            if url.endswith("/docs"):
                return _fetch_result(
                    url,
                    text="<html><body><h1>Documentation</h1></body></html>",
                    headers={"content-type": "text/html"},
                )
            return _fetch_result(url, status_code=404, text="not found")

        context = _context("<html><body><main>Example</main></body></html>")

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=fake_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            result = await check_machine_surfaces(context)

        self.assertIn("Conventional paths found: /docs.", result.finding)

    async def test_api_missing_when_protocols_excluded(self) -> None:
        async def missing_fetch(url: str, **kwargs: object) -> FetchResult:
            return _fetch_result(url, status_code=404, text="not found")

        context = _context("<html><body><main>Example</main></body></html>")

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=missing_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            result = await check_machine_surfaces(
                context,
                include_protocols=False,
            )

        self.assertIn("API path not checked (excluded by scope).", result.finding)

    async def test_pricing_and_integrations_missing_when_commerce_excluded(self) -> None:
        async def missing_fetch(url: str, **kwargs: object) -> FetchResult:
            return _fetch_result(url, status_code=404, text="not found")

        context = _context("<html><body><main>Example</main></body></html>")

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=missing_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            result = await check_machine_surfaces(
                context,
                include_ecommerce=False,
            )

        self.assertIn(
            "Pricing and integrations paths not checked (excluded by scope).",
            result.finding,
        )

    async def test_all_conventional_paths_listed_positively_when_full_scope(self) -> None:
        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            for path in ("/docs", "/pricing", "/integrations", "/api"):
                if url.endswith(path):
                    return _fetch_result(
                        url,
                        text=f"<html><body><h1>{path}</h1></body></html>",
                        headers={"content-type": "text/html"},
                    )
            return _fetch_result(url, status_code=404, text="not found")

        context = _context("<html><body><main>Example</main></body></html>")

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=fake_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            result = await check_machine_surfaces(
                context,
                include_protocols=True,
                include_ecommerce=True,
            )

        self.assertIn(
            "Conventional paths found: /docs, /pricing, /integrations, /api.",
            result.finding,
        )

    async def test_conventional_paths_do_not_change_state_score_or_fix(self) -> None:
        async def with_paths_fetch(url: str, **kwargs: object) -> FetchResult:
            for path in ("/docs", "/pricing", "/integrations", "/api"):
                if url.endswith(path):
                    return _fetch_result(
                        url,
                        text=f"<html><body><h1>{path}</h1></body></html>",
                        headers={"content-type": "text/html"},
                    )
            return _fetch_result(url, status_code=404, text="not found")

        async def without_paths_fetch(url: str, **kwargs: object) -> FetchResult:
            return _fetch_result(url, status_code=404, text="not found")

        context = _context("<html><body><main>Example</main></body></html>")

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=with_paths_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            with_paths_result = await check_machine_surfaces(
                context,
                include_protocols=True,
                include_ecommerce=True,
            )
        with (
            patch("app.checks.machine_surfaces.fetch_url", new=without_paths_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            without_paths_result = await check_machine_surfaces(
                context,
                include_protocols=True,
                include_ecommerce=True,
            )

        self.assertEqual(with_paths_result.state, without_paths_result.state)
        self.assertEqual(with_paths_result.score, without_paths_result.score)
        self.assertEqual(with_paths_result.fix, without_paths_result.fix)
        self.assertIn("Conventional paths found:", with_paths_result.finding)
        self.assertNotIn(
            "Conventional paths found:", without_paths_result.finding
        )

    # ------------------------------------------------------------------
    # Docs-page reachability (Part B)
    # ------------------------------------------------------------------

    async def test_docs_page_linked_surface_drops_orphan_note(self) -> None:
        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            if url.endswith("/llms-full.txt"):
                return _fetch_result(
                    url,
                    text="# Example full export\n\nMachine-readable docs for agents.",
                    headers={"content-type": "text/plain"},
                )
            if url.endswith("/docs"):
                return _fetch_result(
                    url,
                    text=(
                        "<html><body>"
                        '<a href="https://example.com/llms-full.txt">'
                        "LLM export"
                        "</a>"
                        "</body></html>"
                    ),
                    headers={"content-type": "text/html"},
                )
            return _fetch_result(url, status_code=404, text="not found")

        # Homepage does NOT link to /llms-full.txt, so without docs-page
        # reachability this surface would be orphaned.
        context = _context("<html><body><main>Example</main></body></html>")

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=fake_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            result = await check_machine_surfaces(context)

        self.assertNotIn("Reachability note:", result.finding)

    async def test_no_docs_page_keeps_homepage_only_orphan(self) -> None:
        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            if url.endswith("/llms-full.txt"):
                return _fetch_result(
                    url,
                    text="# Example full export\n\nMachine-readable docs for agents.",
                    headers={"content-type": "text/plain"},
                )
            # /docs returns 404
            return _fetch_result(url, status_code=404, text="not found")

        context = _context("<html><body><main>Example</main></body></html>")

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=fake_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            result = await check_machine_surfaces(context)

        self.assertIn("Reachability note: /llms-full.txt", result.finding)

    async def test_docs_page_with_no_anchors_does_not_crash(self) -> None:
        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            if url.endswith("/llms-full.txt"):
                return _fetch_result(
                    url,
                    text="# Example full export\n\nMachine-readable docs for agents.",
                    headers={"content-type": "text/plain"},
                )
            if url.endswith("/docs"):
                return _fetch_result(
                    url,
                    text="<html><body><p>No links here</p></body></html>",
                    headers={"content-type": "text/html"},
                )
            return _fetch_result(url, status_code=404, text="not found")

        context = _context("<html><body><main>Example</main></body></html>")

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=fake_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            # Should not raise; surface remains orphaned.
            result = await check_machine_surfaces(context)

        self.assertIn("Reachability note: /llms-full.txt", result.finding)

    async def test_docs_page_relative_href_resolves_via_make_root_url(self) -> None:
        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            if url.endswith("/llms-full.txt"):
                return _fetch_result(
                    url,
                    text="# Example full export\n\nMachine-readable docs for agents.",
                    headers={"content-type": "text/plain"},
                )
            if url.endswith("/docs"):
                return _fetch_result(
                    url,
                    text=(
                        "<html><body>"
                        '<a href="llms-full.txt">LLM export</a>'
                        "</body></html>"
                    ),
                    headers={"content-type": "text/html"},
                )
            return _fetch_result(url, status_code=404, text="not found")

        context = _context("<html><body><main>Example</main></body></html>")

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=fake_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            result = await check_machine_surfaces(context)

        self.assertNotIn("Reachability note:", result.finding)

    # ------------------------------------------------------------------
    # ai-plugin.json valid + malformed (Part A)
    # ------------------------------------------------------------------

    async def test_ai_plugin_valid_shape_detected(self) -> None:
        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            if url.endswith("/.well-known/ai-plugin.json"):
                return _fetch_result(
                    url,
                    text=(
                        '{"schema_version": "v1", '
                        '"name_for_model": "example", '
                        '"name_for_human": "Example Plugin", '
                        '"api": {"url": "https://example.com/api"}}'
                    ),
                    headers={"content-type": "application/json"},
                )
            return _fetch_result(url, status_code=404, text="not found")

        context = _context("<html><body><main>Example</main></body></html>")

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=fake_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            result = await check_machine_surfaces(
                context,
                include_protocols=True,
            )

        self.assertIn("legacy ai-plugin.json", result.finding)

    async def test_ai_plugin_malformed_rejected(self) -> None:
        async def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            if url.endswith("/.well-known/ai-plugin.json"):
                return _fetch_result(
                    url,
                    text="this is not { valid: json at all <<>>",
                    headers={"content-type": "application/json"},
                )
            return _fetch_result(url, status_code=404, text="not found")

        context = _context("<html><body><main>Example</main></body></html>")

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=fake_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            result = await check_machine_surfaces(
                context,
                include_protocols=True,
            )

        self.assertNotIn("legacy ai-plugin.json", result.finding)

    # ------------------------------------------------------------------
    # NLWeb Schemamap robots directive (Part B)
    # ------------------------------------------------------------------

    async def test_nlweb_schemamap_detected(self) -> None:
        context = AuditContext(
            url=_BASE_URL,
            homepage=_fetch_result(_BASE_URL, text="<html><body><main>Example</main></body></html>"),
            robots=_fetch_result(
                f"{_BASE_URL}/robots.txt",
                status_code=200,
                text=(
                    "User-agent: *\n"
                    "Allow: /\n"
                    "Schemamap: https://example.com/nlweb/schemamap.json\n"
                ),
            ),
            sitemap=_fetch_result(
                f"{_BASE_URL}/sitemap.xml", status_code=404, text="not found"
            ),
        )

        async def missing_fetch(url: str, **kwargs: object) -> FetchResult:
            return _fetch_result(url, status_code=404, text="not found")

        with (
            patch("app.checks.machine_surfaces.fetch_url", new=missing_fetch),
            patch("app.checks.machine_surfaces._dns_aid_surfaces", new=AsyncMock(return_value=[])),
            patch("app.checks.machine_surfaces._ard_dns_hints", new=AsyncMock(return_value=[])),
        ):
            result = await check_machine_surfaces(context)

        self.assertIn("nlweb schemamap", result.finding.lower())


if __name__ == "__main__":
    unittest.main()
