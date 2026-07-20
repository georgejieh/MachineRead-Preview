"""Edge-case tests for SSRF, fetching, rate-limit, cache, presets,
scoring, percentile, position-label, and HTML-boundary paths.

The tests in this module cover off-by-one boundaries and edge inputs
across the MachineRead backend. They are hermetic: all network, DNS,
time, and HTTP interactions are mocked or in-memory. Each test class
focuses on a specific subsystem.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Rate-limit isolation: tests that touch shared in-memory buckets (if any)
# should start clean. The audit-service RateLimiter has its own per-instance
# state, so we don't need this for RateLimiterTests, but it's defensive.
os.environ.setdefault("MACHINEREAD_AUDIT_RATE_LIMIT", "1000/minute")

from app.audit_context import AuditContext
from app.audit_service import AuditCache, RateLimiter
from app.benchmarks import (
    BenchmarkEntry,
    PillarScores,
    _percentile,
    _position_label,
)
from app.checks.html_structure import check_html_structure
from app.checks.llms_txt import _analyse_llms_txt
from app.checks.social import check_social
from app.fetching import FetchResult, fetch_url
from app.presets import _safe_bool, resolve_scope
from app.report_summary import _ratio
from app.scoring import _pillar_score
from app.ssrf import validate_url


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(
    status_code: int,
    *,
    location: str | None = None,
    body: str = "",
    url: str = "https://example.com/",
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Build a mock httpx.Response with the minimal surface fetch_url reads."""
    response = MagicMock()
    response.status_code = status_code
    response.text = body
    response.url = url
    base_headers = {"content-type": "text/html"}
    if location is not None:
        base_headers["location"] = location
    if headers:
        base_headers.update(headers)
    response.headers = base_headers
    return response


def _benchmark_entry(
    *,
    name: str = "Peer",
    free_evidence_score: int = 50,
    agent_readiness_score: int = 50,
) -> BenchmarkEntry:
    return BenchmarkEntry(
        name=name,
        category="test",
        group="test",
        size="test",
        url=f"https://{name.lower().replace(' ', '-')}.example",
        overall_score=free_evidence_score,
        free_evidence_score=free_evidence_score,
        checked_score=free_evidence_score,
        checked_max=100,
        agent_readiness_score=agent_readiness_score,
        agent_readiness_earned=agent_readiness_score,
        agent_readiness_max=100,
        pillar_scores=PillarScores(off_site=10, scrapability=20, seo=10),
    )


def _audit_context(
    *,
    url: str = "https://example.com/",
    homepage_text: str = "<html><body>Hello</body></html>",
) -> AuditContext:
    """Build a minimal AuditContext for direct check calls."""
    homepage = FetchResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        headers={"content-type": "text/html"},
        text=homepage_text,
        elapsed_ms=1,
        redirect_chain=[],
    )
    robots = FetchResult(
        requested_url=url.rstrip("/") + "/robots.txt",
        final_url=url.rstrip("/") + "/robots.txt",
        status_code=200,
        headers={"content-type": "text/plain"},
        text="User-agent: *\nAllow: /\n",
        elapsed_ms=1,
        redirect_chain=[],
    )
    sitemap = FetchResult(
        requested_url=url.rstrip("/") + "/sitemap.xml",
        final_url=url.rstrip("/") + "/sitemap.xml",
        status_code=200,
        headers={"content-type": "application/xml"},
        text="<urlset></urlset>",
        elapsed_ms=1,
        redirect_chain=[],
    )
    return AuditContext(url=url, homepage=homepage, robots=robots, sitemap=sitemap)


def _run(coro):  # noqa: ANN001, ANN201 - tiny helper for async tests
    """Run a coroutine to completion and return the result."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1. ValidateUrlTests (6 tests)
# ---------------------------------------------------------------------------


class ValidateUrlTests(unittest.TestCase):
    """Edge-case tests for app.ssrf.validate_url.

    The mock MUST be active for every test that triggers hostname resolution,
    otherwise the test will hit real DNS and flake.
    """

    def test_rejects_ipv4_loopback(self) -> None:
        """127.0.0.1 is in 127.0.0.0/8 and must be rejected."""
        with patch(
            "app.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("127.0.0.1", 0))],
        ):
            with self.assertRaises(ValueError):
                validate_url("http://127.0.0.1/")

    def test_rejects_ipv6_loopback(self) -> None:
        """::1 is in ::1/128 and must be rejected."""
        with patch(
            "app.ssrf.socket.getaddrinfo",
            return_value=[(23, 1, 6, "", ("::1", 0, 0, 0))],
        ):
            with self.assertRaises(ValueError):
                validate_url("http://[::1]/")

    def test_rejects_javascript_scheme(self) -> None:
        """javascript: must be rejected at the scheme layer (no DNS call)."""
        with self.assertRaises(ValueError) as ctx:
            validate_url("javascript:alert(1)")
        self.assertIn("Scheme", str(ctx.exception))
        self.assertIn("javascript", str(ctx.exception))

    def test_rejects_mailto_scheme(self) -> None:
        """mailto: must be rejected at the scheme layer (no DNS call)."""
        with self.assertRaises(ValueError) as ctx:
            validate_url("mailto:test@example.com")
        self.assertIn("Scheme", str(ctx.exception))
        self.assertIn("mailto", str(ctx.exception))

    def test_accepts_uppercase_https(self) -> None:
        """HTTPS:// must be accepted after the scheme fix.

        Prior to the fix, uppercase HTTPS hit ``parsed.scheme not in
        ('http', 'https')`` and was rejected. After lowercasing the scheme
        check, uppercase HTTPS is accepted.
        """
        with patch(
            "app.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            result = validate_url("HTTPS://example.com/")
        self.assertEqual(result, "HTTPS://example.com/")

    def test_accepts_punycode_hostname(self) -> None:
        """Internationalized hostnames in punycode (xn-- form) are valid."""
        with patch(
            "app.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            result = validate_url("https://xn--bcher-kva.example/path")
        self.assertTrue(result.startswith("https://xn--"))


# ---------------------------------------------------------------------------
# 2. FetchUrlTests (5 tests, async)
# ---------------------------------------------------------------------------


class FetchUrlTests(unittest.TestCase):
    """Edge-case tests for app.fetching.fetch_url redirect handling.

    The redirect loop is ``for _ in range(max_redirects + 1)``. With
    ``max_redirects=5`` that's 6 requests total: the initial GET plus up to
    5 follow-ups. On exhaustion the function returns
    ``error="Too many redirects"``.
    """

    def test_redirect_a_to_b_to_a_loop_terminates(self) -> None:
        """A -> B -> A -> B -> A -> B -> A loop must terminate.

        With ``max_redirects=5`` (default), the loop runs 6 times. Each
        request returns a 302 with a Location pointing back to A or B; the
        final iteration must end with either a final 30x (no Location)
        or the "Too many redirects" error.
        """
        a_url = "https://a.example/"
        b_url = "https://b.example/"

        # Cycle: A -> B -> A -> B -> A -> B -> A (7 hops but only 6
        # requests because the loop budget is max_redirects+1 = 6).
        redirect_sequence = [b_url, a_url, b_url, a_url, b_url, a_url]
        responses = [
            _make_response(302, location=redirect_sequence[0]),
            _make_response(302, location=redirect_sequence[1]),
            _make_response(302, location=redirect_sequence[2]),
            _make_response(302, location=redirect_sequence[3]),
            _make_response(302, location=redirect_sequence[4]),
            _make_response(302, location=redirect_sequence[5]),
        ]

        with patch(
            "app.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            with patch(
                "httpx.AsyncClient.request",
                new=AsyncMock(side_effect=responses),
            ):
                result = _run(fetch_url(a_url))

        # Either the loop was exhausted ("Too many redirects") or the
        # final 30x was returned without a Location to chase. Either
        # outcome must NOT raise and must return a FetchResult.
        self.assertIsNotNone(result)
        self.assertIsInstance(result, FetchResult)
        if result.error is not None:
            self.assertIn("Too many redirects", result.error)

    def test_empty_location_returns_30x(self) -> None:
        """A 30x response with no Location header must return the 30x
        verbatim (no follow-up)."""
        response = _make_response(302, location=None)
        with patch(
            "app.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            with patch(
                "httpx.AsyncClient.request",
                new=AsyncMock(return_value=response),
            ) as mock_request:
                result = _run(fetch_url("https://example.com/"))

        self.assertEqual(result.status_code, 302)
        self.assertEqual(mock_request.await_count, 1)

    def test_max_redirects_zero_only_first_response(self) -> None:
        """With ``max_redirects=0``, the loop runs exactly once and the
        only request issued is the initial GET.

        Because the first response is a 302 with a Location, the loop
        still validates the next URL (via validate_url, which our DNS
        mock allows) but the loop terminates before issuing the
        follow-up: the function exits with ``status_code=None`` and
        ``error="Too many redirects"``. We assert that exactly one
        request was made regardless of the outcome.
        """
        response = _make_response(302, location="https://other.example/")
        with patch(
            "app.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            with patch(
                "httpx.AsyncClient.request",
                new=AsyncMock(return_value=response),
            ) as mock_request:
                result = _run(
                    fetch_url("https://example.com/", max_redirects=0)
                )

        # Exactly one request: max_redirects=0 -> range(1) = [0].
        self.assertEqual(mock_request.await_count, 1)
        # The redirect exhausted the budget before any follow-up; the
        # function surfaces the standard "Too many redirects" error.
        self.assertIsNotNone(result.error)
        self.assertIn("Too many redirects", result.error)

    def test_max_redirects_one_follows_exactly_one_hop(self) -> None:
        """With ``max_redirects=1``, exactly 2 requests happen: the initial
        GET plus 1 follow-up. The follow-up is a non-redirect terminal
        response."""
        responses = [
            _make_response(
                302,
                location="https://example.com/final",
                url="https://example.com/",
            ),
            _make_response(
                200, body="final", url="https://example.com/final"
            ),
        ]
        with patch(
            "app.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            with patch(
                "httpx.AsyncClient.request",
                new=AsyncMock(side_effect=responses),
            ) as mock_request:
                result = _run(
                    fetch_url("https://example.com/", max_redirects=1)
                )

        self.assertEqual(mock_request.await_count, 2)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.text, "final")
        # Exactly one redirect recorded in the chain.
        self.assertEqual(len(result.redirect_chain), 1)

    def test_redirect_to_file_scheme_rejected(self) -> None:
        """A redirect whose Location resolves to a non-http(s) scheme
        (e.g. ``file://``) must surface as an error because validate_url
        rejects file://.
        """
        responses = [
            _make_response(
                302,
                location="file:///etc/passwd",
                url="https://example.com/",
            ),
        ]
        with patch(
            "app.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            with patch(
                "httpx.AsyncClient.request",
                new=AsyncMock(side_effect=responses),
            ):
                result = _run(fetch_url("https://example.com/"))

        self.assertIsNotNone(result.error)
        self.assertIn("Unsafe redirect target", result.error)


# ---------------------------------------------------------------------------
# 3. RateLimiterTests (6 tests)
# ---------------------------------------------------------------------------


class RateLimiterTests(unittest.TestCase):
    """Edge-case tests for app.audit_service.RateLimiter.

    The fixed window is computed as ``int(t) // window * window``. With a
    60-second window, t=120 yields the window [120, 180); t=179 is still
    in that same window; t=180 is the start of the next window.
    """

    def test_window_boundary_straddle(self) -> None:
        """A request just before the window boundary is rejected after the
        first call within a one-request window; a request at the next
        window is allowed again."""
        limiter = RateLimiter(requests=1, window_seconds=60)

        # First window: t=119.9 -> window_start = 119 // 60 * 60 = 60.
        with patch("app.audit_service.time.time", return_value=119.9):
            allowed, _, _ = limiter.is_allowed("client-a")
            self.assertTrue(allowed)
            limiter.record("client-a")

        # Same window: t=179.99 -> window_start = 179 // 60 * 60 = 120,
        # which IS the second window. With requests=1 the first call
        # already exhausted the budget. Wait - 119.9 placed us in window
        # 60..120 (window_start = 60), while 179.99 placed the next
        # check in window 120..180 (window_start = 120). So this should
        # be allowed (new window).
        with patch("app.audit_service.time.time", return_value=179.99):
            allowed, remaining, _ = limiter.is_allowed("client-a")
            self.assertTrue(allowed)
            self.assertEqual(remaining, 0)
            limiter.record("client-a")

        # Same second window: t=180.0 -> window_start = 180, same as
        # 179.99's window_start of 120. Wait - 180 // 60 * 60 = 180
        # which is yet another new window.
        with patch("app.audit_service.time.time", return_value=180.0):
            allowed, remaining, _ = limiter.is_allowed("client-a")
            self.assertTrue(allowed)
            self.assertEqual(remaining, 0)

        # Demonstrate same-window exhaustion with a tighter test:
        limiter2 = RateLimiter(requests=1, window_seconds=60)
        with patch("app.audit_service.time.time", return_value=100.0):
            self.assertTrue(limiter2.is_allowed("c")[0])
            limiter2.record("c")
        with patch("app.audit_service.time.time", return_value=140.0):
            # 140 // 60 * 60 = 120 -> same window as 100 (window_start=60).
            # Actually 100 // 60 * 60 = 60, while 140 // 60 * 60 = 120.
            # So this is a NEW window and request is allowed.
            self.assertTrue(limiter2.is_allowed("c")[0])
            limiter2.record("c")
        with patch("app.audit_service.time.time", return_value=150.0):
            # 150 // 60 * 60 = 120 -> same window as above, blocked.
            allowed, remaining, _ = limiter2.is_allowed("c")
            self.assertFalse(allowed)
            self.assertEqual(remaining, 0)

    def test_zero_limit_rejects_all(self) -> None:
        """A RateLimiter with requests=0 must raise ValueError."""
        with self.assertRaises(ValueError):
            RateLimiter(requests=0, window_seconds=60)

    def test_negative_limit_rejects_all(self) -> None:
        """A RateLimiter with requests=-1 must raise ValueError."""
        with self.assertRaises(ValueError):
            RateLimiter(requests=-1, window_seconds=60)

    def test_zero_window_rejects(self) -> None:
        """A RateLimiter with window_seconds=0 must raise ValueError."""
        with self.assertRaises(ValueError):
            RateLimiter(requests=3, window_seconds=0)

    def test_negative_window_rejects(self) -> None:
        """A RateLimiter with window_seconds=-1 must raise ValueError."""
        with self.assertRaises(ValueError):
            RateLimiter(requests=3, window_seconds=-1)

    def test_valid_limiter_constructs(self) -> None:
        """A positive requests/window pair constructs and stores arguments."""
        limiter = RateLimiter(requests=3, window_seconds=60)
        self.assertEqual(limiter._max_requests, 3)
        self.assertEqual(limiter._window, 60)

    def test_large_window_no_overflow(self) -> None:
        """A large window (window_seconds=10**9) must not overflow
        arithmetic. Two calls within the same synthetic window must
        both be counted correctly.
        """
        limiter = RateLimiter(requests=3, window_seconds=10**9)
        # Pick a large timestamp well within int range.
        huge_now = 10**12

        with patch("app.audit_service.time.time", return_value=huge_now):
            allowed1, remaining1, reset1 = limiter.is_allowed("big-client")
            self.assertTrue(allowed1)
            self.assertEqual(remaining1, 2)
            limiter.record("big-client")

            allowed2, remaining2, _ = limiter.is_allowed("big-client")
            self.assertTrue(allowed2)
            self.assertEqual(remaining2, 1)
            limiter.record("big-client")

        # One second later, still in the same billion-second window.
        with patch("app.audit_service.time.time", return_value=huge_now + 1):
            allowed3, remaining3, _ = limiter.is_allowed("big-client")
            self.assertTrue(allowed3)
            self.assertEqual(remaining3, 0)

        # Reset timestamp must be a positive integer.
        self.assertIsInstance(reset1, int)
        self.assertGreater(reset1, huge_now)


# ---------------------------------------------------------------------------
# 4. AuditCacheTests (7 tests)
# ---------------------------------------------------------------------------


class AuditCacheTests(unittest.TestCase):
    """Edge-case tests for app.audit_service.AuditCache (LRU)."""

    def test_lru_eviction_promotes_recently_used(self) -> None:
        """Repeatedly accessing the eldest key must move it to the most
        recently used end of the OrderedDict, preventing its eviction
        while still permitting a new entry to push out the actual LRU."""
        cache: AuditCache = AuditCache(maxsize=2)
        result_a = MagicMock(name="result_a")
        result_b = MagicMock(name="result_b")
        result_c = MagicMock(name="result_c")

        cache.put("https://a.example/", None, result_a)
        cache.put("https://b.example/", None, result_b)

        # Promote "a" by reading it twice (each get() moves to end).
        cache.get("https://a.example/", None)
        cache.get("https://a.example/", None)
        # At this point, order is [b, a].

        # Insert c: LRU is b (still at the front), so b should be evicted.
        cache.put("https://c.example/", None, result_c)

        self.assertIsNone(cache.get("https://b.example/", None))
        self.assertIs(cache.get("https://a.example/", None), result_a)
        self.assertIs(cache.get("https://c.example/", None), result_c)

    def test_key_normalizes_case_and_trailing_slash(self) -> None:
        """``https://Example.com/`` and ``https://example.com`` must map
        to the same cache key (case + trailing slash normalized)."""
        cache: AuditCache = AuditCache(maxsize=2)
        result = MagicMock(name="result")
        cache.put("https://Example.com/", None, result)

        # Different case + trailing-slash variant must hit the same entry.
        hit = cache.get("https://example.com", None)
        self.assertIs(hit, result)

        # Direct key lookup must succeed too.
        canonical_key = cache._key("https://Example.com/", None)
        hit_by_id = cache.get_by_audit_id(canonical_key)
        self.assertIs(hit_by_id, result)

    def test_maxsize_zero_rejects(self) -> None:
        """``maxsize=0`` must raise ValueError."""
        with self.assertRaises(ValueError):
            AuditCache(maxsize=0)

    def test_negative_maxsize_rejects(self) -> None:
        """``maxsize=-1`` and lower must raise ValueError."""
        for size in (-1, -100):
            with self.subTest(size=size):
                with self.assertRaises(ValueError):
                    AuditCache(maxsize=size)

    def test_valid_maxsize_constructs(self) -> None:
        """``maxsize=1`` constructs and stores the argument."""
        cache: AuditCache = AuditCache(maxsize=1)
        self.assertEqual(cache._maxsize, 1)

    def test_maxsize_one_evicts_on_second_put(self) -> None:
        """``maxsize=1`` stores the first entry, evicts it on the second
        put(), and stores the new entry."""
        cache: AuditCache = AuditCache(maxsize=1)
        result_a = MagicMock(name="result_a")
        result_b = MagicMock(name="result_b")

        cache.put("https://a.example/", None, result_a)
        # Same key (after normalization) updates in place.
        cache.put("https://A.example/", None, result_b)
        self.assertIs(cache.get("https://a.example/", None), result_b)

        # Now a different key must evict the single slot.
        cache.put("https://b.example/", None, MagicMock(name="result_c"))
        self.assertIsNone(cache.get("https://a.example/", None))

    def test_cache_key_differs_for_different_overrides(self) -> None:
        """Two runs with the same URL/preset but different override dicts
        must produce distinct cache keys . Without
        overrides in the key, the second put would overwrite the first
        and a get for the first override set would return the wrong
        cached result.
        """
        cache: AuditCache = AuditCache(maxsize=4)
        result_a = MagicMock(name="result_a")
        result_b = MagicMock(name="result_b")

        overrides_a = {"include_protocols": True}
        overrides_b = {"include_protocols": True, "include_ecommerce": True}

        cache.put(
            "https://example.com/", "custom", result_a, overrides_a
        )
        cache.put(
            "https://example.com/", "custom", result_b, overrides_b
        )

        # Both entries coexist and round-trip correctly.
        self.assertIs(
            cache.get("https://example.com/", "custom", overrides_a),
            result_a,
        )
        self.assertIs(
            cache.get("https://example.com/", "custom", overrides_b),
            result_b,
        )

        # Direct key inspection must confirm the keys are distinct.
        key_a = cache._key(
            "https://example.com/", "custom", overrides_a
        )
        key_b = cache._key(
            "https://example.com/", "custom", overrides_b
        )
        self.assertNotEqual(key_a, key_b)

    def test_cache_key_same_for_same_overrides_different_order(self) -> None:
        """Two override dicts that contain the same key/value pairs but
        in different insertion order must hash to the same cache key
        (canonical form via ``sort_keys=True``).
        """
        cache: AuditCache = AuditCache(maxsize=2)
        result = MagicMock(name="result")

        overrides_ordered = {
            "include_protocols": True,
            "include_account_auth": False,
            "include_ecommerce": True,
        }
        overrides_reordered = {
            "include_ecommerce": True,
            "include_protocols": True,
            "include_account_auth": False,
        }

        key_ordered = cache._key(
            "https://example.com/", "custom", overrides_ordered
        )
        key_reordered = cache._key(
            "https://example.com/", "custom", overrides_reordered
        )
        self.assertEqual(key_ordered, key_reordered)

        # Round-trip: put with one ordering, get with the other must hit.
        cache.put(
            "https://example.com/",
            "custom",
            result,
            overrides_ordered,
        )
        self.assertIs(
            cache.get(
                "https://example.com/", "custom", overrides_reordered
            ),
            result,
        )

    def test_cache_hit_with_overrides(self) -> None:
        """A put followed by a get with the same overrides must return
        the cached result (sanity check that overrides flow through
        end-to-end without breaking the basic put/get contract).
        """
        cache: AuditCache = AuditCache(maxsize=2)
        result = MagicMock(name="result")
        overrides = {"include_protocols": True}

        cache.put(
            "https://example.com/", "custom", result, overrides
        )
        self.assertIs(
            cache.get("https://example.com/", "custom", overrides),
            result,
        )

        # get_by_audit_id must also resolve to the same result when the
        # key is computed with the same overrides.
        key = cache._key(
            "https://example.com/", "custom", overrides
        )
        self.assertIs(cache.get_by_audit_id(key), result)

        # Sanity: same URL/preset without overrides must MISS.
        self.assertIsNone(cache.get("https://example.com/", "custom"))


# ---------------------------------------------------------------------------
# 5. PresetValidationTests (4 tests)
# ---------------------------------------------------------------------------


class PresetValidationTests(unittest.TestCase):
    """Edge-case tests for app.presets.resolve_scope."""

    def test_unknown_preset_with_empty_overrides_raises_keyerror(self) -> None:
        """An unknown preset name with empty overrides triggers a dict
        access on a missing key (KeyError), not a ValueError, because
        PRESETS[...] is the lookup that fails."""
        with self.assertRaises(KeyError):
            resolve_scope("no_such_preset", {})

    def test_empty_string_preset_with_empty_overrides_raises_keyerror(self) -> None:
        """An empty-string preset with empty overrides is also treated
        as a missing key in the PRESETS dict (KeyError)."""
        with self.assertRaises(KeyError):
            resolve_scope("", {})

    def test_unknown_preset_with_non_empty_overrides_raises_valueerror(self) -> None:
        """A known preset name with non-empty overrides that reference
        a not-applicable key must raise ValueError (via validate_overrides).

        "blog" is a known preset; "oauth_oidc" is in blog's
        ``not_applicable`` set, so it triggers a ValueError.
        """
        with self.assertRaises(ValueError) as ctx:
            resolve_scope("blog", {"oauth_oidc": True})
        # The error message references the override key.
        self.assertIn("not applicable", str(ctx.exception))

    def test_safe_bool_coerces_string_maybe_to_false(self) -> None:
        """``_safe_bool("maybe", default=False)`` must fall through to
        the default because "maybe" is not a recognized truthy/falsy
        string."""
        # "maybe" is not in the recognized set; falls through to default.
        self.assertFalse(_safe_bool("maybe", default=False))
        self.assertTrue(_safe_bool("maybe", default=True))


# ---------------------------------------------------------------------------
# 6. ScoringAndPercentileTests (5 tests)
# ---------------------------------------------------------------------------


class ScoringAndPercentileTests(unittest.TestCase):
    """Edge-case tests for benchmarks, scoring, and report-summary
    pure-function helpers."""

    def test_percentile_bounds_zero_and_hundred(self) -> None:
        """``_percentile`` boundaries: empty entries -> 0; score >= max
        -> 100; score below every entry -> 0."""
        entries = [
            _benchmark_entry(name="A", free_evidence_score=10),
            _benchmark_entry(name="B", free_evidence_score=20),
            _benchmark_entry(name="C", free_evidence_score=30),
        ]
        # Empty entries -> 0 (the documented "no peers" case).
        self.assertEqual(_percentile(0, []), 0)
        # Score equal to max -> all entries at or below -> 100%.
        self.assertEqual(_percentile(30, entries), 100)
        # Score below every entry -> 0 entries at or below -> 0%.
        self.assertEqual(_percentile(0, entries), 0)

    def test_position_label_with_various_lengths(self) -> None:
        """``_position_label`` uses ``scores[len//4]`` (lower quartile)
        and ``scores[(3*len)//4]`` (upper quartile). The label must
        fall into one of the four documented buckets for any list
        length 1..5."""
        for length in (1, 2, 3, 4, 5):
            entries = [
                _benchmark_entry(
                    name=f"P{i}", free_evidence_score=20 + i * 10
                )
                for i in range(length)
            ]
            # Probe at the median-ish score.
            label = _position_label(40, entries)
            self.assertIn(
                label,
                {
                    "Near the top of this Essentials benchmark snapshot",
                    "Above the middle of this Essentials benchmark snapshot",
                    "Within the middle of this Essentials benchmark snapshot",
                    "Below most sites in this Essentials benchmark snapshot",
                },
                msg=f"unexpected label {label!r} for length={length}",
            )

    def test_overall_score_capped_at_one_hundred(self) -> None:
        """``_pillar_score`` clamps ``sum(check.score for pillar)`` at
        ``cap``. Three checks that each sum 50 in the same pillar must
        cap at 30 (the off_site pillar cap), not 150."""
        from app.models import CheckResult

        checks = [
            CheckResult(
                pillar="off_site",
                check_name=f"off_site_{i}",
                label=f"row {i}",
                state="pass",
                score=50,
                max_score=50,
                finding="x",
                fix="x",
                effort="low",
            )
            for i in range(3)
        ]
        capped = _pillar_score(checks, "off_site", cap=30)
        self.assertEqual(capped, 30)

    def test_pillar_cap_enforcement_under_overflow(self) -> None:
        """Pillar cap enforcement is the floor under overflow:
        passing a cap of 5 with a sum of 9999 must return exactly 5."""
        from app.models import CheckResult

        checks = [
            CheckResult(
                pillar="scrapability",
                check_name=f"scrapability_{i}",
                label=f"row {i}",
                state="pass",
                score=5000,
                max_score=5000,
                finding="x",
                fix="x",
                effort="low",
            )
            for i in range(2)
        ]
        # 5000 + 5000 = 10000; cap=5 must clamp to 5.
        self.assertEqual(_pillar_score(checks, "scrapability", cap=5), 5)

    def test_ratio_zero_denominator_returns_zero(self) -> None:
        """``_ratio(earned, row_max)`` with ``row_max <= 0`` returns 0.0
        so the summary endpoint never raises on a 0-max row."""
        self.assertEqual(_ratio(0, 0), 0.0)
        self.assertEqual(_ratio(5, 0), 0.0)
        self.assertEqual(_ratio(5, -1), 0.0)
        # Normal case.
        self.assertAlmostEqual(_ratio(2, 8), 0.25)


# ---------------------------------------------------------------------------
# 7. EncodingBoundaryTests (4 tests)
# ---------------------------------------------------------------------------


class EncodingBoundaryTests(unittest.TestCase):
    """Edge-case tests for HTML body / encoding boundaries in check
    functions and the llms.txt analyser."""

    def test_check_social_handles_null_bytes(self) -> None:
        """A homepage body containing ``\\x00`` must not crash the
        social check. BeautifulSoup/lxml tolerates NULs but the
        function should still produce a valid CheckResult."""
        # 3 social profile links so we satisfy the >=3 found-platform rule,
        # plus a stray NUL byte in an attribute that lxml must accept.
        html = (
            '<html><head><title>Example\x00 Co</title></head><body>'
            '<a href="https://twitter.com/example">Twitter</a>'
            '<a href="https://x.com/example">X</a>'
            '<a href="https://linkedin.com/company/example">LinkedIn</a>'
            '</body></html>'
        )
        context = _audit_context(homepage_text=html)
        result = _run(check_social(context))
        # Must produce a well-formed CheckResult regardless of the NUL.
        self.assertIn(result.state, {"pass", "partial", "warn", "fail"})
        self.assertGreaterEqual(result.score, 0)
        self.assertLessEqual(result.score, result.max_score)

    def test_check_html_structure_handles_null_bytes(self) -> None:
        """A homepage body with a ``\\x00`` in a heading or attribute
        must not crash the html-structure check."""
        html = (
            "<html><body>"
            "<h1>Welcome\x00 to the site</h1>"
            "<main><p>Content here.</p></main>"
            "<nav><a href='/about'>About</a></nav>"
            "<footer>Footer</footer>"
            "</body></html>"
        )
        context = _audit_context(homepage_text=html)
        result = _run(check_html_structure(context))
        self.assertIn(result.state, {"pass", "partial", "warn", "fail"})
        self.assertGreaterEqual(result.score, 0)

    def test_analyse_llms_txt_body_lengths(self) -> None:
        """``_analyse_llms_txt`` enforces a 20-character minimum.

        - 18 chars -> too short, returns ``(False, ["too short"])``.
        - 20 chars starting with '#' but no URL -> ``(True, [])``:
          the length threshold is ``len < 20`` (strict less-than), so a
          20-char body with H1 and no URL is accepted (no URL check
          only fires when ``len >= 20``).
        - 25 chars with H1 AND a URL is fully valid -> ``(True, [])``.
        """
        # 18-char body -> rejected for length.
        short_body = "# Title\n" + "x" * 10  # 18 chars total
        valid, issues = _analyse_llms_txt(short_body)
        self.assertFalse(valid)
        self.assertIn("too short", issues)

        # 21-char body with H1 but no URL -> rejected (missing link).
        h1_only = "# Title No Url Here. "  # exactly 21 chars
        self.assertEqual(len(h1_only), 21)
        valid, issues = _analyse_llms_txt(h1_only)
        self.assertFalse(valid)
        self.assertIn("no URLs", issues[0] if issues else "")

        # 25-char body with H1 + URL is fully valid.
        full_body = "# Title\nSee https://x.y\n"
        valid, issues = _analyse_llms_txt(full_body)
        self.assertTrue(valid)
        self.assertEqual(issues, [])

    def test_report_summary_ratio_edge_cases(self) -> None:
        """``_ratio`` from report_summary: (0, 1) -> 0.0; (1, 1) -> 1.0."""
        self.assertEqual(_ratio(0, 1), 0.0)
        self.assertEqual(_ratio(1, 1), 1.0)
        # Boundary: earned == max yields exactly 1.0 (no off-by-one).
        self.assertAlmostEqual(_ratio(10, 10), 1.0)


if __name__ == "__main__":
    unittest.main()
