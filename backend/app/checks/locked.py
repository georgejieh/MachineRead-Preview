from app.models import CheckResult


def locked_checks(include_ecommerce: bool = False) -> list[CheckResult]:
    """Return rubric checks reserved for advanced coverage."""
    workflow_finding = (
        "Not verified in Essentials. Simulating product search, cart, checkout, "
        "and post-purchase workflows requires browser automation."
        if include_ecommerce
        else "Not verified in Essentials. Simulating conversion, booking, lead, "
        "account, or form workflows requires browser automation."
    )
    workflow_fix = (
        "Unlock Pro to run commerce agent simulations and locate the exact failure step."
        if include_ecommerce
        else "Unlock Pro to run agent workflow simulations and locate the exact failure step."
    )

    return [
        CheckResult(
            pillar="off_site",
            check_name="earned_mentions_backlinks",
            label="Earned Mentions & Backlinks",
            state="locked",
            evidence_level="not_applicable",
            available_in="Starter",
            score=0,
            max_score=8,
            finding=(
                "Not verified in Essentials. This requires external citation, "
                "backlink, press, and community mention data."
            ),
            fix="Unlock Starter to compare earned authority signals against same-tier peers.",
            effort="high",
        ),
        CheckResult(
            pillar="off_site",
            check_name="owned_social_presence",
            label="Owned Social Presence Discovery",
            state="locked",
            evidence_level="not_applicable",
            available_in="Starter",
            score=0,
            max_score=6,
            finding=(
                "Not verified in Essentials. The included scan verifies profile links "
                "published by the audited site; discovering unlinked official social profiles "
                "requires off-site search or platform data."
            ),
            fix="Unlock Starter to discover official social profiles that are not linked from the site.",
            effort="medium",
        ),
        CheckResult(
            pillar="off_site",
            check_name="social_traction_reviews",
            label="Social Traction & Review Footprint",
            state="locked",
            evidence_level="not_applicable",
            available_in="Starter",
            score=0,
            max_score=6,
            finding=(
                "Not verified in Essentials. The included scan can see linked profiles, "
                "but not their traction, review volume, or peer-relative activity."
            ),
            fix="Unlock Starter to measure review/profile traction and compare it with similar brands.",
            effort="medium",
        ),
        CheckResult(
            pillar="off_site",
            check_name="ai_citation_share",
            label="AI Citation Share of Voice",
            state="locked",
            evidence_level="not_applicable",
            available_in="Pro",
            score=0,
            max_score=4,
            finding=(
                "Not verified in Essentials. Measuring real AI citation rates "
                "requires running controlled prompts across search-enabled models."
            ),
            fix="Unlock Pro to measure actual AI citation frequency and peer citation gaps.",
            effort="high",
        ),
        CheckResult(
            pillar="scrapability",
            check_name="extraction_fidelity",
            label="Deep Extraction Fidelity",
            state="locked",
            evidence_level="not_applicable",
            available_in="Starter",
            score=0,
            max_score=4,
            finding=(
                "Not verified in Essentials. The included scan inspects raw HTML, "
                "but does not compare extracted content quality through advanced crawler tooling."
            ),
            fix="Unlock Starter to compare crawler-extracted content against the rendered page.",
            effort="medium",
        ),
        CheckResult(
            pillar="scrapability",
            check_name="agent_task_simulation",
            label="Agent Commerce Simulation" if include_ecommerce else "Agent Workflow Simulation",
            state="locked",
            evidence_level="not_applicable",
            available_in="Pro",
            score=0,
            max_score=3,
            finding=workflow_finding,
            fix=workflow_fix,
            effort="high",
        ),
        CheckResult(
            pillar="seo",
            check_name="multi_engine_index_coverage",
            label="Google/Bing/Brave Index Coverage",
            state="locked",
            evidence_level="not_applicable",
            available_in="Starter",
            score=0,
            max_score=5,
            finding=(
                "Not verified in Essentials. The included scan checks discovery signals, "
                "but not whether Google, Bing, and Brave actually index the site."
            ),
            fix="Unlock Starter to verify index coverage and crawl visibility across search engines.",
            effort="medium",
        ),
        CheckResult(
            pillar="seo",
            check_name="core_web_vitals",
            label="Core Web Vitals & Field Performance",
            state="locked",
            evidence_level="not_applicable",
            available_in="Starter",
            score=0,
            max_score=5,
            finding=(
                "Not verified in Essentials. The included scan inspects HTML performance proxies, "
                "but does not use CrUX, Lighthouse, Search Console, or field performance data."
            ),
            fix="Unlock Starter to validate Core Web Vitals and real performance data against peers.",
            effort="medium",
        ),
        CheckResult(
            pillar="seo",
            check_name="keyword_competitor_gap",
            label="Keyword & Competitor Gap",
            state="locked",
            evidence_level="not_applicable",
            available_in="Starter",
            score=0,
            max_score=3,
            finding=(
                "Not verified in Essentials. Keyword rankings, competitor gaps, "
                "and query-level opportunity require search result data."
            ),
            fix="Unlock Starter to compare query coverage against same-tier peers.",
            effort="medium",
        ),
    ]
