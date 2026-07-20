from dataclasses import dataclass


@dataclass(frozen=True)
class EssentialsCheckGroup:
    pillar: str
    check_name: str
    label: str
    max_score: int


ESSENTIALS_CHECK_GROUPS: tuple[EssentialsCheckGroup, ...] = (
    EssentialsCheckGroup("off_site", "social", "Social & Entity Metadata", 2),
    EssentialsCheckGroup("off_site", "wikipedia", "Wikipedia & Wikidata Entity", 4),
    EssentialsCheckGroup("scrapability", "robots_txt", "AI Bot Policy Signals", 6),
    EssentialsCheckGroup("scrapability", "bot_access", "MachineRead Bot Fetch Access", 6),
    EssentialsCheckGroup(
        "scrapability",
        "html_structure",
        "Semantic HTML & Agent Navigation",
        4,
    ),
    EssentialsCheckGroup("scrapability", "schema_ld", "JSON-LD Structured Data", 5),
    EssentialsCheckGroup("scrapability", "llms_txt", "LLM Text & Markdown Access", 5),
    EssentialsCheckGroup("scrapability", "ssr", "Raw HTML Readability", 4),
    EssentialsCheckGroup("scrapability", "machine_surfaces", "Agent Protocol Discovery", 3),
    EssentialsCheckGroup("seo", "pagespeed", "Crawl Efficiency & HTML Performance", 3),
    EssentialsCheckGroup("seo", "canonical", "Canonical & HTTPS", 5),
    EssentialsCheckGroup("seo", "indexing", "Indexing Directives", 5),
    EssentialsCheckGroup("seo", "search_discovery", "Search Discovery Hints", 4),
)

ESSENTIALS_CHECK_GROUP_COUNT = len(ESSENTIALS_CHECK_GROUPS)
ESSENTIALS_CHECKED_MAX = sum(group.max_score for group in ESSENTIALS_CHECK_GROUPS)
