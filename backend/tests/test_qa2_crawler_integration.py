import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.crawler_registry import (
    CRAWLERS,
    active_crawler_caveats,
    bot_user_agents,
    crawler_directive_sources,
    fetch_probe_names,
    robots_scoring_crawlers,
)
from app.checks.bot_access import check_bot_access
from app.checks.indexing import check_indexing
from app.checks.robots import _classify_bot_access, check_robots
from app.fetching import BROWSER_USER_AGENT, FetchResult
from backend.tests.fixtures import make_audit_context


class QA2CrawlerRegistryTests(unittest.TestCase):
    def test_duckduckgo_crawlers_are_active_in_all_three_consumers(self) -> None:
        crawlers = {crawler.name: crawler for crawler in CRAWLERS}
        scored_names = {crawler.name for crawler in robots_scoring_crawlers()}
        probe_names = set(fetch_probe_names())
        directive_sources = crawler_directive_sources()

        self.assertEqual(len(scored_names), 15)
        self.assertEqual(len(probe_names), 9)
        self.assertEqual(len(directive_sources), 17)
        for name in ("DuckDuckBot", "DuckAssistBot"):
            crawler = crawlers[name]
            self.assertTrue(crawler.robots_scored)
            self.assertTrue(crawler.fetch_probe)
            self.assertTrue(crawler.directive_source)
            self.assertIn(name, scored_names)
            self.assertIn(name, probe_names)
            self.assertIn(name.lower(), directive_sources)

        self.assertEqual(
            crawlers["DuckDuckBot"].fetch_user_agent,
            "DuckDuckBot/1.1; (+http://duckduckgo.com/duckduckbot.html)",
        )
        self.assertEqual(
            crawlers["DuckAssistBot"].fetch_user_agent,
            "DuckAssistBot/1.2; (+http://duckduckgo.com/duckassistbot.html)",
        )
        self.assertEqual(
            crawlers["DuckDuckBot"].official_documentation_url,
            "https://duckduckgo.com/duckduckgo-help-pages/results/duckduckbot",
        )
        self.assertEqual(
            crawlers["DuckAssistBot"].official_ip_list_url,
            "https://duckduckgo.com/duckassistbot.json",
        )
        self.assertEqual(
            crawlers["DuckDuckBot"].official_ip_list_url,
            "https://duckduckgo.com/duckduckbot.json",
        )
        self.assertEqual(
            crawlers["DuckAssistBot"].official_documentation_url,
            "https://duckduckgo.com/duckduckgo-help-pages/results/duckassistbot",
        )

    def test_consumer_caveats_are_scoped_and_concise(self) -> None:
        fetch_caveats = " ".join(active_crawler_caveats("fetch"))
        robots_caveats = " ".join(active_crawler_caveats("robots"))
        directive_caveats = " ".join(
            active_crawler_caveats(
                "directive",
                crawler_tokens={"duckassistbot"},
            )
        )

        self.assertIn("do not authenticate provider IPs", fetch_caveats)
        self.assertIn("real-time retrieval for AI-assisted answers", fetch_caveats)
        self.assertIn("not model training", fetch_caveats)
        self.assertIn("organic search ranking or result inclusion", fetch_caveats)
        self.assertIn("Google-Extended is a robots.txt control token", robots_caveats)
        self.assertIn("ChatGPT-User is user-triggered", robots_caveats)
        self.assertNotIn("cannot authenticate official DuckDuckGo traffic", robots_caveats)
        self.assertNotIn("provider IPs", directive_caveats)
        self.assertIn("real-time retrieval for AI-assisted answers", directive_caveats)


class QA2CrawlerBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_robots_scores_duckassist_policy_with_purpose_caveat(self) -> None:
        robots_text = (
            "User-agent: *\nAllow: /\n"
            "User-agent: DuckAssistBot\nDisallow: /"
        )
        context = make_audit_context(robots_text=robots_text)

        access = _classify_bot_access(robots_text, context.url + "/")
        result = await check_robots(context)

        self.assertEqual(access["DuckAssistBot"], "explicit_blocked")
        self.assertEqual(result.state, "partial")
        self.assertEqual(result.score, 5)
        self.assertIn("DuckAssistBot", result.finding)
        self.assertIn("real-time retrieval for AI-assisted answers", result.finding)
        self.assertIn("not model training", result.finding)
        self.assertIn("organic search ranking or result inclusion", result.finding)
        self.assertNotIn("do not authenticate provider IPs", result.finding)

    async def test_fetch_matrix_probes_exact_duckduckgo_user_agents_and_caveats(self) -> None:
        body = "<html><body><main>" + ("Public crawler content. " * 100) + "</main></body></html>"
        context = make_audit_context(homepage_html=body)
        requested_user_agents: list[str] = []

        async def fake_fetch(
            url: str,
            user_agent: str = "",
            **kwargs: object,
        ) -> FetchResult:
            requested_user_agents.append(user_agent)
            status_code = 403 if user_agent.startswith("DuckAssistBot/1.2") else 200
            return FetchResult(
                requested_url=url,
                final_url=url,
                status_code=status_code,
                headers={"content-type": "text/html"},
                text=body,
                elapsed_ms=1,
                redirect_chain=[],
            )

        with patch("app.checks.bot_access.fetch_url", new=fake_fetch):
            result = await check_bot_access(context)

        user_agents = bot_user_agents()
        self.assertEqual(requested_user_agents[0], BROWSER_USER_AGENT)
        self.assertIn(user_agents["DuckDuckBot"], requested_user_agents)
        self.assertIn(user_agents["DuckAssistBot"], requested_user_agents)
        self.assertEqual(result.state, "partial")
        self.assertEqual(result.score, 5)
        self.assertIn("DuckAssistBot (HTTP 403)", result.finding)
        self.assertIn("do not authenticate provider IPs", result.finding)
        self.assertIn("real-time retrieval for AI-assisted answers", result.finding)
        self.assertIn("organic search ranking or result inclusion", result.finding)

    async def test_indexing_reads_duckassist_directive_with_scoped_caveat(self) -> None:
        context = make_audit_context(
            homepage_html=(
                '<html><head><meta name="DuckAssistBot" content="noindex"></head>'
                "<body>Example</body></html>"
            )
        )

        result = await check_indexing(context)

        self.assertEqual(result.state, "fail")
        self.assertEqual(result.score, 0)
        self.assertIn("meta duckassistbot", result.finding)
        self.assertIn("real-time retrieval for AI-assisted answers", result.finding)
        self.assertIn("not model training", result.finding)
        self.assertIn("organic search ranking or result inclusion", result.finding)
        self.assertNotIn("provider IPs", result.finding)


if __name__ == "__main__":
    unittest.main()
