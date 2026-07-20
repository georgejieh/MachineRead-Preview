from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal


CrawlerPurpose = Literal[
    "search and retrieval",
    "training and corpus",
    "user-triggered agents",
    "media search",
]
CrawlerConsumer = Literal["robots", "fetch", "directive"]


@dataclass(frozen=True)
class CrawlerIdentity:
    name: str
    provider: str
    purpose: CrawlerPurpose
    fetch_user_agent: str | None = None
    official_documentation_url: str | None = None
    official_ip_list_url: str | None = None
    robots_scored: bool = True
    fetch_probe: bool = False
    directive_source: bool = True
    caveat: str | None = None

    @property
    def token(self) -> str:
        return self.name


CRAWLER_GROUP_WEIGHTS: dict[CrawlerPurpose, float] = {
    "search and retrieval": 2.5,
    "training and corpus": 1.5,
    "user-triggered agents": 1.0,
    "media search": 1.0,
}

CRAWLERS: tuple[CrawlerIdentity, ...] = (
    CrawlerIdentity(
        name="Googlebot",
        provider="Google",
        purpose="search and retrieval",
        fetch_user_agent="Googlebot/2.1 (+http://www.google.com/bot.html)",
        fetch_probe=True,
    ),
    CrawlerIdentity(
        name="Bingbot",
        provider="Microsoft Bing",
        purpose="search and retrieval",
        fetch_user_agent="Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
        fetch_probe=True,
    ),
    CrawlerIdentity(
        name="DuckDuckBot",
        provider="DuckDuckGo",
        purpose="search and retrieval",
        fetch_user_agent="DuckDuckBot/1.1; (+http://duckduckgo.com/duckduckbot.html)",
        official_documentation_url=(
            "https://duckduckgo.com/duckduckgo-help-pages/results/duckduckbot"
        ),
        official_ip_list_url="https://duckduckgo.com/duckduckbot.json",
        robots_scored=True,
        fetch_probe=True,
        directive_source=True,
        caveat=(
            "A user-agent-only probe cannot authenticate official DuckDuckGo traffic "
            "without IP verification against DuckDuckGo's official IP list."
        ),
    ),
    CrawlerIdentity(
        name="DuckAssistBot",
        provider="DuckDuckGo",
        purpose="search and retrieval",
        fetch_user_agent="DuckAssistBot/1.2; (+http://duckduckgo.com/duckassistbot.html)",
        official_documentation_url=(
            "https://duckduckgo.com/duckduckgo-help-pages/results/duckassistbot"
        ),
        official_ip_list_url="https://duckduckgo.com/duckassistbot.json",
        robots_scored=True,
        fetch_probe=True,
        directive_source=True,
        caveat=(
            "A user-agent-only probe cannot authenticate official DuckDuckGo traffic "
            "without IP verification against DuckDuckGo's official IP list. "
            "DuckAssistBot performs real-time retrieval for AI-assisted answers, not "
            "model training. Blocking DuckAssistBot does not affect organic search "
            "ranking or result inclusion, and the change may take up to 72 hours."
        ),
    ),
    CrawlerIdentity(
        name="OAI-SearchBot",
        provider="OpenAI",
        purpose="search and retrieval",
        fetch_user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 "
            "Safari/537.36; compatible; OAI-SearchBot/1.3; "
            "+https://openai.com/searchbot"
        ),
        fetch_probe=True,
    ),
    CrawlerIdentity(
        name="Claude-SearchBot",
        provider="Anthropic",
        purpose="search and retrieval",
    ),
    CrawlerIdentity(
        name="PerplexityBot",
        provider="Perplexity",
        purpose="search and retrieval",
        fetch_user_agent=(
            "Mozilla/5.0 AppleWebKit/537.36 "
            "(KHTML, like Gecko; compatible; PerplexityBot/1.0; "
            "+https://perplexity.ai/perplexitybot)"
        ),
        fetch_probe=True,
    ),
    CrawlerIdentity(
        name="GPTBot",
        provider="OpenAI",
        purpose="training and corpus",
        fetch_user_agent=(
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); "
            "compatible; GPTBot/1.3; +https://openai.com/gptbot"
        ),
        fetch_probe=True,
    ),
    CrawlerIdentity(
        name="ClaudeBot",
        provider="Anthropic",
        purpose="training and corpus",
        fetch_user_agent="ClaudeBot/1.0",
        fetch_probe=True,
    ),
    CrawlerIdentity(
        name="CCBot",
        provider="Common Crawl",
        purpose="training and corpus",
        fetch_user_agent="CCBot/2.0 (https://commoncrawl.org/faq/)",
        fetch_probe=True,
    ),
    CrawlerIdentity(
        name="Google-Extended",
        provider="Google",
        purpose="training and corpus",
        caveat=(
            "Google-Extended is a robots.txt control token; Google says it "
            "does not have a separate HTTP user-agent string."
        ),
    ),
    CrawlerIdentity(
        name="ChatGPT-User",
        provider="OpenAI",
        purpose="user-triggered agents",
        fetch_user_agent=(
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); "
            "compatible; ChatGPT-User/1.0; +https://openai.com/bot"
        ),
        caveat="ChatGPT-User is user-triggered and may not be governed like automatic crawling.",
    ),
    CrawlerIdentity(
        name="Claude-User",
        provider="Anthropic",
        purpose="user-triggered agents",
        caveat="Claude-User is user-triggered and may not be governed like automatic crawling.",
    ),
    CrawlerIdentity(
        name="Perplexity-User",
        provider="Perplexity",
        purpose="user-triggered agents",
        fetch_user_agent=(
            "Mozilla/5.0 AppleWebKit/537.36 "
            "(KHTML, like Gecko; compatible; Perplexity-User/1.0; "
            "+https://perplexity.ai/perplexity-user)"
        ),
        caveat="Perplexity-User is user-triggered and may not be governed like automatic crawling.",
    ),
    CrawlerIdentity(
        name="Googlebot-Image",
        provider="Google",
        purpose="media search",
        fetch_user_agent="Googlebot-Image/1.0",
    ),
    CrawlerIdentity(
        name="Googlebot-Video",
        provider="Google",
        purpose="media search",
        fetch_user_agent="Googlebot-Video/1.0",
        robots_scored=False,
    ),
    CrawlerIdentity(
        name="Googlebot-News",
        provider="Google",
        purpose="media search",
        robots_scored=False,
    ),
)

UNVERIFIED_PROVIDER_CAVEATS: tuple[str, ...] = (
    "Brave Search is covered through universal crawlability and locked real index coverage; "
    "MachineRead does not score a guessed Brave-specific crawler token without a current "
    "verified primary-source token.",
)


def robots_scoring_crawlers() -> tuple[CrawlerIdentity, ...]:
    return tuple(crawler for crawler in CRAWLERS if crawler.robots_scored)


def crawler_groups() -> dict[CrawlerPurpose, tuple[str, ...]]:
    groups: dict[CrawlerPurpose, list[str]] = {
        group: [] for group in CRAWLER_GROUP_WEIGHTS
    }
    for crawler in robots_scoring_crawlers():
        groups[crawler.purpose].append(crawler.token)
    return {group: tuple(tokens) for group, tokens in groups.items()}


def bot_user_agents() -> dict[str, str]:
    return {
        crawler.token: crawler.fetch_user_agent
        for crawler in CRAWLERS
        if crawler.fetch_user_agent is not None
    }


def fetch_probe_names() -> tuple[str, ...]:
    return tuple(crawler.token for crawler in CRAWLERS if crawler.fetch_probe)


def crawler_directive_sources() -> set[str]:
    return {
        crawler.token.lower()
        for crawler in CRAWLERS
        if crawler.directive_source
    }


def active_crawler_caveats(
    consumer: CrawlerConsumer,
    *,
    crawler_tokens: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Return concise caveats for crawlers active in a specific consumer."""
    selected_tokens = (
        {token.lower() for token in crawler_tokens}
        if crawler_tokens is not None
        else None
    )
    active_tokens = {
        crawler.token
        for crawler in CRAWLERS
        if (
            (consumer == "robots" and crawler.robots_scored)
            or (consumer == "fetch" and crawler.fetch_probe)
            or (consumer == "directive" and crawler.directive_source)
        )
        and (selected_tokens is None or crawler.token.lower() in selected_tokens)
    }

    caveats: list[str] = []
    if consumer == "fetch" and active_tokens & {"DuckDuckBot", "DuckAssistBot"}:
        caveats.append(
            "MachineRead user-agent probes do not authenticate provider IPs, including "
            "DuckDuckGo's published crawler ranges."
        )
    if "DuckAssistBot" in active_tokens:
        caveats.append(
            "DuckAssistBot performs real-time retrieval for AI-assisted answers, not "
            "model training; blocking it does not establish an impact on organic search "
            "ranking or result inclusion."
        )
    if consumer == "robots":
        caveats.extend(
            crawler.caveat
            for crawler in CRAWLERS
            if crawler.token in active_tokens
            and crawler.token not in {"DuckDuckBot", "DuckAssistBot"}
            and crawler.caveat
        )
    if consumer in {"robots", "fetch"} and selected_tokens is None:
        caveats.extend(UNVERIFIED_PROVIDER_CAVEATS)
    return tuple(caveats)


def crawler_registry_caveat() -> str:
    return " ".join(active_crawler_caveats("robots"))
