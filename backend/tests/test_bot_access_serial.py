"""serialized bot probes with 429 retry.

These tests target :func:`app.checks.bot_access._probe_bots` and
:func:`app.checks.bot_access._probe_one_bot` directly so we can assert the
new pacing + retry contract without spinning up the full check or doing real
HTTP. Sequencing is verified via mock side effects (``call_in_flight`` and
``call_order``) rather than wall-clock timing, because we mock
``asyncio.sleep`` and do not want the suite to take ~4.5 real seconds.
"""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.checks import bot_access
from app.checks.bot_access import (
    _RETRY_DELAY,
    _STAGGER_DELAY,
    _probe_bots,
    _probe_one_bot,
)
from app.fetching import FetchResult
from backend.tests.fixtures import EXAMPLE_URL, make_audit_context, make_fetch_result


# A small deterministic matrix — we do not need the full 9-bot registry here.
# The production code reads ``_MATRIX_BOTS`` from the registry, but for these
# tests the function takes the matrix as an argument so we can keep the test
# small and explicit.
_TEST_MATRIX = ["BotA", "BotB", "BotC"]
_TEST_USER_AGENTS = {
    "BotA": "BotA/1.0 (+https://example.com/a)",
    "BotB": "BotB/1.0 (+https://example.com/b)",
    "BotC": "BotC/1.0 (+https://example.com/c)",
}


def _ok_fetch(url: str = EXAMPLE_URL, status_code: int = 200, text: str = "<html><body>ok</body></html>") -> FetchResult:
    """Return a minimal successful ``FetchResult`` for the test url."""
    return make_fetch_result(url, text, status_code=status_code)


def _body_with_words(word_count: int = 100) -> str:
    """Return a body with at least ``word_count`` words so the thin-response
    check in ``_blocked_reason`` does not flag it as blocked.
    """
    sentence = "Accessible homepage content for agents and search. "
    repeats = max(1, word_count // len(sentence.split()))
    return "<html><body>" + sentence * repeats + "</body></html>"


def _baseline() -> tuple[FetchResult, bot_access.RoutingSnapshot, int]:
    """Return a (baseline_fetch, baseline_snapshot, baseline_words) triple."""
    fetch = _ok_fetch(text=_body_with_words(120))
    snapshot = bot_access._routing_snapshot(fetch)
    return fetch, snapshot, snapshot.visible_words


class ProbeBotsSequentialTests(unittest.IsolatedAsyncioTestCase):
    """Probes must run sequentially with a stagger between each, not via ``gather``."""

    async def test_probes_execute_sequentially_not_concurrently(self) -> None:
        """With ``asyncio.gather`` the in-flight count would hit the matrix length.
        With the new serial loop it should never exceed 1.
        """
        baseline_fetch, baseline_snapshot, baseline_words = _baseline()
        call_order: list[str] = []
        in_flight = 0
        max_in_flight = 0
        started = asyncio.Event()
        can_finish = asyncio.Event()
        bot_body = _body_with_words(120)

        async def fake_fetch(url: str, user_agent: str = "", **_kwargs: object) -> FetchResult:
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            call_order.append(f"start:{user_agent}")
            if len(call_order) == 1:
                # First probe: signal start, then hold until released. This
                # gives any subsequent (concurrent) probe a chance to run
                # before we return, which ``gather`` would exploit. With the
                # new serial loop no second probe enters until ``can_finish``
                # is set and we exit.
                started.set()
                await can_finish.wait()
            call_order.append(f"end:{user_agent}")
            in_flight -= 1
            return _ok_fetch(text=bot_body)

        with (
            patch("app.checks.bot_access.fetch_url", new=fake_fetch),
            patch("app.checks.bot_access.asyncio.sleep", new=AsyncMock()),
        ):
            task = asyncio.create_task(
                _probe_bots(
                    EXAMPLE_URL,
                    _TEST_USER_AGENTS,
                    _TEST_MATRIX,
                    baseline_fetch=baseline_fetch,
                    baseline_snapshot=baseline_snapshot,
                    baseline_words=baseline_words,
                )
            )
            # Give the first probe time to enter and signal.
            await started.wait()
            # Tiny yield to let any rogue concurrent probe sneak in. Under
            # ``asyncio.gather`` it would; under the serial loop it would not.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertEqual(in_flight, 1, "Second probe ran while the first was still in-flight (gather burst!)")
            can_finish.set()
            blocked, routing_differences, accessible_count = await task

        # Under the serial contract, max concurrency is exactly 1.
        self.assertEqual(max_in_flight, 1)
        # All 3 probes finished.
        self.assertEqual(accessible_count, 3)
        self.assertEqual(blocked, [])
        self.assertEqual(routing_differences, [])
        # The call order must interleave start/end for each bot (no two
        # starts in a row before any end), proving serial execution.
        starts = [c for c in call_order if c.startswith("start:")]
        ends = [c for c in call_order if c.startswith("end:")]
        self.assertEqual(starts[0], ends[0].replace("end:", "start:"))
        self.assertEqual(len(starts), len(_TEST_MATRIX))
        self.assertEqual(len(ends), len(_TEST_MATRIX))

    async def test_stagger_delay_applied_between_probes(self) -> None:
        """``asyncio.sleep`` is called with ``_STAGGER_DELAY`` between probes,
        exactly ``len(matrix) - 1`` times in the initial wave.
        """
        baseline_fetch, baseline_snapshot, baseline_words = _baseline()
        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        async def fake_fetch(url: str, user_agent: str = "", **_kwargs: object) -> FetchResult:
            return _ok_fetch(text=_body_with_words(120))

        with (
            patch("app.checks.bot_access.fetch_url", new=fake_fetch),
            patch("app.checks.bot_access.asyncio.sleep", new=fake_sleep),
        ):
            await _probe_bots(
                EXAMPLE_URL,
                _TEST_USER_AGENTS,
                _TEST_MATRIX,
                baseline_fetch=baseline_fetch,
                baseline_snapshot=baseline_snapshot,
                baseline_words=baseline_words,
            )

        # 3 probes -> 2 staggers between them.
        self.assertEqual(sleep_calls, [_STAGGER_DELAY] * (len(_TEST_MATRIX) - 1))
        # Sanity: the stagger constant is the configured 0.5s.
        self.assertEqual(_STAGGER_DELAY, 0.5)


class ProbeOneBotRetryTests(unittest.IsolatedAsyncioTestCase):
    """HTTP 429 must trigger exactly one retry after ``_RETRY_DELAY``."""

    async def test_429_first_call_retried_uses_second_result(self) -> None:
        """First call returns 429, second returns 200. The retry result is used."""
        call_log: list[str] = []
        sleep_log: list[float] = []

        async def fake_fetch(url: str, user_agent: str = "", **_kwargs: object) -> FetchResult:
            call_log.append(user_agent)
            if len(call_log) == 1:
                return _ok_fetch(status_code=429)
            return _ok_fetch(status_code=200)

        async def fake_sleep(seconds: float) -> None:
            sleep_log.append(seconds)

        with (
            patch("app.checks.bot_access.fetch_url", new=fake_fetch),
            patch("app.checks.bot_access.asyncio.sleep", new=fake_sleep),
        ):
            result = await _probe_one_bot(EXAMPLE_URL, _TEST_USER_AGENTS["BotA"])

        # Exactly one retry → fetch_url called twice, with the same UA.
        self.assertEqual(len(call_log), 2)
        self.assertEqual(call_log[0], call_log[1])
        # The retry delay was awaited once, with the configured value.
        self.assertEqual(sleep_log, [_RETRY_DELAY])
        # And the returned FetchResult is the second (200) one, not the 429.
        self.assertEqual(result.status_code, 200)

    async def test_429_retry_exhausted_counted_as_blocked(self) -> None:
        """Both calls return 429 → caller receives 429 → counted as blocked."""
        async def fake_fetch(url: str, user_agent: str = "", **_kwargs: object) -> FetchResult:
            return _ok_fetch(status_code=429)

        baseline_fetch, baseline_snapshot, baseline_words = _baseline()
        call_count = 0

        async def counting_fetch(url: str, user_agent: str = "", **_kwargs: object) -> FetchResult:
            nonlocal call_count
            call_count += 1
            return _ok_fetch(status_code=429)

        with (
            patch("app.checks.bot_access.fetch_url", new=counting_fetch),
            patch("app.checks.bot_access.asyncio.sleep", new=AsyncMock()),
        ):
            blocked, _, accessible_count = await _probe_bots(
                EXAMPLE_URL,
                _TEST_USER_AGENTS,
                _TEST_MATRIX,
                baseline_fetch=baseline_fetch,
                baseline_snapshot=baseline_snapshot,
                baseline_words=baseline_words,
            )

        # Every probe 429'd, so each one retried once → 2 * 3 = 6 fetch calls.
        self.assertEqual(call_count, 2 * len(_TEST_MATRIX))
        self.assertEqual(accessible_count, 0)
        self.assertEqual(len(blocked), len(_TEST_MATRIX))
        # Each entry follows ``"BotName (HTTP 429)"`` — the literal reason
        # emitted by ``_blocked_reason`` when status_code is in {401,403,429,503}.
        for entry in blocked:
            self.assertIn("HTTP 429", entry)

    async def test_429_retry_distinct_from_stagger_delay(self) -> None:
        """The retry sleep uses ``_RETRY_DELAY`` (2.0s) and the inter-probe
        sleep uses ``_STAGGER_DELAY`` (0.5s); they must never be conflated.
        """
        sleep_log: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_log.append(seconds)

        # Force every probe to 429 so both delays fire on every iteration.
        async def always_429(url: str, user_agent: str = "", **_kwargs: object) -> FetchResult:
            return _ok_fetch(status_code=429)

        baseline_fetch, baseline_snapshot, baseline_words = _baseline()
        with (
            patch("app.checks.bot_access.fetch_url", new=always_429),
            patch("app.checks.bot_access.asyncio.sleep", new=fake_sleep),
        ):
            await _probe_bots(
                EXAMPLE_URL,
                _TEST_USER_AGENTS,
                _TEST_MATRIX,
                baseline_fetch=baseline_fetch,
                baseline_snapshot=baseline_snapshot,
                baseline_words=baseline_words,
            )

        # Per probe: 1 retry sleep at _RETRY_DELAY. Between probes:
        # (matrix_len - 1) stagger sleeps at _STAGGER_DELAY.
        expected = (
            [_RETRY_DELAY] * len(_TEST_MATRIX)        # one retry per probe
            + [_STAGGER_DELAY] * (len(_TEST_MATRIX) - 1)  # N-1 staggers
        )
        # The retry sleeps happen *inside* each probe (before the next stagger),
        # so the actual order is: retry_1, stagger, retry_2, stagger, retry_3.
        # We only need to assert the values and counts here, not the order.
        self.assertEqual(sorted(sleep_log), sorted(expected))
        self.assertEqual(sleep_log.count(_RETRY_DELAY), len(_TEST_MATRIX))
        self.assertEqual(sleep_log.count(_STAGGER_DELAY), len(_TEST_MATRIX) - 1)
        # And the two constants really are distinct, so a regression that
        # accidentally reuses one for the other would show up here too.
        self.assertNotEqual(_STAGGER_DELAY, _RETRY_DELAY)


class CheckBotAccessContractTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end: ``check_bot_access`` still scores correctly under serialization."""

    async def test_passing_matrix_still_scores_6_of_6(self) -> None:
        """Sanity check that the serialization wrapper preserves the existing
        ``pass`` / score=6 outcome on a healthy matrix.
        """
        body = "<html><body>" + ("healthy homepage content for agents. " * 80) + "</body></html>"
        context = make_audit_context(homepage_html=body)

        async def fake_fetch(url: str, user_agent: str = "", **_kwargs: object) -> FetchResult:
            return _ok_fetch(text=body)

        with (
            patch("app.checks.bot_access.fetch_url", new=fake_fetch),
            patch("app.checks.bot_access.asyncio.sleep", new=AsyncMock()),
        ):
            from app.checks.bot_access import check_bot_access

            result = await check_bot_access(context)

        self.assertEqual(result.state, "pass")
        self.assertEqual(result.score, 6)
        self.assertEqual(result.max_score, 6)


if __name__ == "__main__":
    unittest.main()
