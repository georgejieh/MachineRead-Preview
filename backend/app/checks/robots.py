import urllib.robotparser
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse

from app.audit_context import AuditContext
from app.crawler_registry import (
    CRAWLER_GROUP_WEIGHTS,
    active_crawler_caveats,
    crawler_groups,
    robots_scoring_crawlers,
)
from app.models import CheckResult

_BOT_NAMES = [crawler.token for crawler in robots_scoring_crawlers()]
_BOT_GROUPS = crawler_groups()
_BOT_GROUP_WEIGHTS = CRAWLER_GROUP_WEIGHTS

_TEMPLATES = {
    "all_allowed": (
        "Major search, AI crawler, and user-triggered agent fetchers can access "
        "the homepage according to robots.txt.{content_signal_detail}",
        "No action needed. Your robots.txt is well-configured for AI crawlers.",
    ),
    "some_blocked": (
        "Some crawlers or agent fetchers are blocked in robots.txt: {blocked}. "
        "Others are accessible through explicit or wildcard rules.{content_signal_detail}",
        "Allow the search and AI bots you want to serve. Prefer explicit rules "
        "for known agents and rate-limit abusive traffic at the edge.",
    ),
    "all_blocked": (
        "All tracked search and AI crawlers are blocked in robots.txt.{content_signal_detail}",
        "Remove blanket Disallow rules or add explicit Allow rules for the bots "
        "you want to support.",
    ),
    "fetch_error": (
        "Could not fetch robots.txt. Bots will assume no restrictions, but the "
        "absence of an explicit file is a missed opportunity to signal openness.",
        "Create a robots.txt at your domain root. Explicitly allow AI bots and "
        "reference your sitemap.",
    ),
}

_CONTENT_SIGNAL_KEYS = {"content-signal", "content-signals"}
_CONTENT_SIGNAL_TOKENS = {"search", "ai-input", "ai-train", "tdm-reservation"}
_ROBOTS_SIZE_WARNING_BYTES = 500 * 1024


@dataclass(frozen=True)
class RobotsQualitySignals:
    byte_size: int
    oversized: bool
    malformed_line_count: int
    empty_group_count: int
    invalid_sitemap_count: int
    crawl_delay_count: int
    invalid_crawl_delay_count: int


def _robot_groups(content: str) -> list[tuple[list[str], list[tuple[str, str]]]]:
    groups: list[tuple[list[str], list[tuple[str, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[str, str]] = []

    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip().lower()

        if key == "user-agent":
            if agents and rules:
                groups.append((agents, rules))
                agents = []
                rules = []
            agents.append(value)
        elif key in {"allow", "disallow"} and agents:
            rules.append((key, value))

    if agents:
        groups.append((agents, rules))

    return groups


def _mentioned_agent(groups: list[tuple[list[str], list[tuple[str, str]]]], bot: str) -> str | None:
    bot_name = bot.lower()
    if any(bot_name in agents for agents, _ in groups):
        return "exact"
    if any("*" in agents for agents, _ in groups):
        return "wildcard"
    return None


def nlweb_schemamap_found(robots_text: str) -> bool:
    """Return True when ``robots_text`` declares an NLWeb ``Schemamap:`` directive.

    Mirrors the Agentmap pattern: looks for ``Schemamap:`` after stripping
    inline comments and lower-casing the key. Operates purely on the
    already-fetched robots body — does not consult the live network.
    """
    if not robots_text:
        return False
    for raw_line in robots_text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key = line.split(":", 1)[0].strip().lower()
        if key == "schemamap":
            return True
    return False


def _classify_bot_access(content: str, url: str) -> dict[str, str]:
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(content.splitlines())
    groups = _robot_groups(content)

    access: dict[str, str] = {}
    for bot in _BOT_NAMES:
        mentioned = _mentioned_agent(groups, bot)
        can_fetch = parser.can_fetch(bot, url)

        if mentioned == "exact":
            access[bot] = "explicit_allowed" if can_fetch else "explicit_blocked"
        elif mentioned == "wildcard":
            access[bot] = "wildcard_allowed" if can_fetch else "wildcard_blocked"
        else:
            access[bot] = "not_mentioned"

    return access


def _content_signal_tokens_from_value(value: str) -> set[str]:
    lowered = value.lower()
    return {token for token in _CONTENT_SIGNAL_TOKENS if token in lowered}


def content_signal_tokens(content: str) -> list[str]:
    found: set[str] = set()
    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip().lower() in _CONTENT_SIGNAL_KEYS:
            found.update(_content_signal_tokens_from_value(value))

    return sorted(found)


def response_content_signal_tokens(headers: Mapping[str, str]) -> list[str]:
    found: set[str] = set()
    for key, value in headers.items():
        if key.lower() in _CONTENT_SIGNAL_KEYS:
            found.update(_content_signal_tokens_from_value(value))
    return sorted(found)


def robots_quality_signals(content: str) -> RobotsQualitySignals:
    byte_size = len(content.encode("utf-8"))
    malformed_line_count = 0
    empty_group_count = 0
    invalid_sitemap_count = 0
    crawl_delay_count = 0
    invalid_crawl_delay_count = 0
    current_agents: list[str] = []
    current_access_rule_count = 0

    def close_group() -> None:
        nonlocal empty_group_count, current_agents, current_access_rule_count
        if current_agents and current_access_rule_count == 0:
            empty_group_count += 1
        current_agents = []
        current_access_rule_count = 0

    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            malformed_line_count += 1
            continue

        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if not key:
            malformed_line_count += 1
            continue

        if key == "user-agent":
            if current_agents and current_access_rule_count:
                close_group()
            current_agents.append(value.lower())
            continue

        if key in {"allow", "disallow"}:
            if not current_agents:
                malformed_line_count += 1
                continue
            current_access_rule_count += 1
            continue

        if key == "sitemap":
            parsed = urlparse(value)
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
                invalid_sitemap_count += 1
            continue

        if key == "crawl-delay":
            crawl_delay_count += 1
            if not current_agents:
                malformed_line_count += 1
            try:
                delay = float(value)
            except ValueError:
                invalid_crawl_delay_count += 1
                continue
            if delay < 0:
                invalid_crawl_delay_count += 1
            continue

    close_group()
    return RobotsQualitySignals(
        byte_size=byte_size,
        oversized=byte_size > _ROBOTS_SIZE_WARNING_BYTES,
        malformed_line_count=malformed_line_count,
        empty_group_count=empty_group_count,
        invalid_sitemap_count=invalid_sitemap_count,
        crawl_delay_count=crawl_delay_count,
        invalid_crawl_delay_count=invalid_crawl_delay_count,
    )


def explicit_ai_bot_count(content: str) -> int:
    groups = _robot_groups(content)
    explicit_agents = {
        agent
        for agents, _ in groups
        for agent in agents
        if agent != "*"
    }
    return sum(1 for bot in _BOT_NAMES if bot.lower() in explicit_agents)


def _content_signal_detail(content: str, *header_sets: Mapping[str, str]) -> str:
    robots_tokens = content_signal_tokens(content)
    response_tokens = sorted(
        {
            token
            for headers in header_sets
            for token in response_content_signal_tokens(headers)
        }
    )
    details = []
    if robots_tokens:
        details.append("robots.txt Content Signals: " + ", ".join(robots_tokens))
    if response_tokens:
        details.append("HTTP Content-Signal headers: " + ", ".join(response_tokens))
    if not details:
        return ""
    return " Content Signals are also present (" + "; ".join(details) + ")."


def _quality_issues(signals: RobotsQualitySignals) -> list[str]:
    issues: list[str] = []
    if signals.oversized:
        issues.append("robots.txt is over the 500 KiB crawler parsing limit")
    if signals.malformed_line_count:
        issues.append(f"{signals.malformed_line_count} malformed directive line(s)")
    if signals.empty_group_count:
        issues.append(f"{signals.empty_group_count} user-agent group(s) have no Allow or Disallow rule")
    if signals.invalid_sitemap_count:
        issues.append(f"{signals.invalid_sitemap_count} invalid Sitemap directive(s)")
    if signals.invalid_crawl_delay_count:
        issues.append(f"{signals.invalid_crawl_delay_count} invalid Crawl-delay directive(s)")
    return issues


def _quality_detail(signals: RobotsQualitySignals) -> str:
    issues = _quality_issues(signals)
    if issues:
        return " Robots.txt quality issues: " + "; ".join(issues) + "."
    detail = " Robots.txt quality checks passed for file size, directive syntax, and Sitemap lines."
    if signals.crawl_delay_count:
        detail += (
            f" {signals.crawl_delay_count} Crawl-delay directive(s) are present as "
            "crawl pacing hints for crawlers that honor them."
        )
    return detail


def _quality_fix(signals: RobotsQualitySignals) -> str:
    actions: list[str] = []
    if signals.oversized:
        actions.append("keep robots.txt under 500 KiB")
    if signals.malformed_line_count or signals.empty_group_count:
        actions.append("make every user-agent group parse cleanly with Allow or Disallow rules")
    if signals.invalid_sitemap_count:
        actions.append("use absolute HTTP(S) URLs in Sitemap directives")
    if signals.invalid_crawl_delay_count:
        actions.append("use non-negative numeric Crawl-delay values or remove them")
    if not actions:
        return ""
    return " Also " + "; ".join(actions) + "."


def _registry_caveat_detail() -> str:
    caveat = " ".join(active_crawler_caveats("robots"))
    return f" Registry caveat: {caveat}" if caveat else ""


def _quality_penalty(signals: RobotsQualitySignals) -> int:
    penalty = 0
    if signals.oversized:
        penalty += 1
    if signals.malformed_line_count or signals.empty_group_count:
        penalty += 1
    if signals.invalid_sitemap_count:
        penalty += 1
    if signals.invalid_crawl_delay_count:
        penalty += 1
    return min(2, penalty)


def _score_access(access: dict[str, str]) -> int:
    # Design note (Fable 5 review): ``not_mentioned`` earns 0.7 (not 0.0)
    # because silent non-mention is the standard practice for benign crawlers,
    # not active blocking. Most sites omit explicit ``Allow`` rules for benign
    # bots because robots.txt default behaviour already permits uncited
    # user-agents. Scoring silent omission at 0.0 would push well-behaved sites
    # below 4/6 on the access subscore for a non-issue. We therefore reserve:
    #   * 0.0-0.6 for EXPLICITLY blocked bots (``Disallow`` rules)
    #   * 0.7-0.79 for bots not mentioned at all (default-permit)
    #   * 0.8-0.9 for bots that explicitly allow wildcards
    #   * 1.0 for bots that explicitly allow narrow user-agents
    state_weights = {
        "explicit_allowed": 1.0,
        "wildcard_allowed": 0.8,
        "not_mentioned": 0.7,
        "explicit_blocked": 0.0,
        "wildcard_blocked": 0.0,
    }
    total = 0.0
    maximum = 0.0
    for group, bots in _BOT_GROUPS.items():
        group_weight = _BOT_GROUP_WEIGHTS[group]
        available_bots = [bot for bot in bots if bot in access]
        if not available_bots:
            continue
        group_score = sum(state_weights[access[bot]] for bot in available_bots) / len(available_bots)
        total += group_score * group_weight
        maximum += group_weight
    return round(total / maximum * 6) if maximum else 0


def _blocked_by_group(access: dict[str, str]) -> list[str]:
    blocked_groups: list[str] = []
    for group, bots in _BOT_GROUPS.items():
        blocked = [
            bot
            for bot in bots
            if access.get(bot) in {"explicit_blocked", "wildcard_blocked"}
        ]
        if blocked:
            blocked_groups.append(f"{group}: {', '.join(blocked)}")
    return blocked_groups


async def check_robots(context: AuditContext) -> CheckResult:
    """Check which search and AI bots are allowed in robots.txt."""
    robots_signal_text = context.robots.text if context.robots.ok else ""
    content_signal_detail = _content_signal_detail(
        robots_signal_text,
        context.robots.headers,
        context.homepage.headers,
    )
    if not context.robots.ok or not context.robots.text.strip():
        finding, fix = _TEMPLATES["fetch_error"]
        finding += content_signal_detail
        finding += _registry_caveat_detail()
        return CheckResult(
            pillar="scrapability",
            check_name="robots_txt",
            label="AI Bot Policy Signals",
            state="warn",
            evidence_level="unknown",
            score=3 if content_signal_detail else 2,
            max_score=6,
            finding=finding,
            fix=fix,
            effort="low",
        )

    access = _classify_bot_access(context.robots.text, context.url + "/")
    blocked = [
        bot
        for bot, access_state in access.items()
        if access_state in {"explicit_blocked", "wildcard_blocked"}
    ]
    blocked_groups = _blocked_by_group(access)
    explicit = [bot for bot, access_state in access.items() if access_state == "explicit_allowed"]
    score = _score_access(access)
    if content_signal_detail:
        score = min(6, score + 1)
    quality = robots_quality_signals(context.robots.text)
    quality_issues = _quality_issues(quality)
    score = max(0, score - _quality_penalty(quality))

    if not blocked:
        state = "pass"
        finding, fix = _TEMPLATES["all_allowed"]
        finding = finding.format(content_signal_detail=content_signal_detail)
        if len(explicit) < len(access):
            state = "partial"
            finding = (
                "No tracked crawlers are blocked, but only "
                f"{len(explicit)} of {len(access)} are explicitly named. "
                "Unmentioned bots are allowed by default."
                f"{content_signal_detail}"
            )
    elif len(blocked) == len(access):
        state = "fail"
        finding, fix = _TEMPLATES["all_blocked"]
        finding = finding.format(content_signal_detail=content_signal_detail)
    else:
        state = "partial"
        finding, fix = _TEMPLATES["some_blocked"]
        blocked_detail = "; ".join(blocked_groups) if blocked_groups else ", ".join(blocked)
        finding = finding.format(blocked=blocked_detail, content_signal_detail=content_signal_detail)

    if quality_issues and state == "pass":
        state = "partial"
    finding += _quality_detail(quality)
    finding += _registry_caveat_detail()
    fix += _quality_fix(quality)

    return CheckResult(
        pillar="scrapability",
        check_name="robots_txt",
        label="AI Bot Policy Signals",
        state=state,
        score=score,
        max_score=6,
        finding=finding,
        fix=fix,
        effort="low",
    )
