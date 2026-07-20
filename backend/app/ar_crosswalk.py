"""MachineRead to AgentReady.org requirement-ID crosswalk.

community checklist alignment, not certification.
AgentReady.org spec v1.0.0 (https://www.agentready.org/spec.json).

Mapping is metadata-only — no API fields, no new rows, no new weights.
"""

# Evidence maturity labels used in public crosswalk docs.
# Kept as a constant so docs and code cannot drift.
_MATURITY_LABEL_ESSENTIALS = "Essentials-scored"
_MATURITY_LABEL_STRICT_AGENT = "strict-agent-readiness"
_MATURITY_LABEL_TRACKED = "tracked-only"
_MATURITY_LABEL_LOCKED = "locked/paid"
_MATURITY_LABEL_NA = "not applicable"

MATURITY_LABELS: tuple[str, ...] = (
    _MATURITY_LABEL_ESSENTIALS,
    _MATURITY_LABEL_STRICT_AGENT,
    _MATURITY_LABEL_TRACKED,
    _MATURITY_LABEL_LOCKED,
    _MATURITY_LABEL_NA,
)

# Essentials rows → AgentReady.org requirement stable IDs.
# Each check_name maps to the applicable AR-* IDs, or an empty tuple when none apply.
AR_CROSSWALK: dict[str, tuple[str, ...]] = {
    "social": ("AR-CONT-01",),
    "wikipedia": (),
    "robots_txt": ("AR-DISC-01", "AR-IDEN-01"),
    "bot_access": (),
    "html_structure": ("AR-CONT-01", "AR-CONT-04"),
    "schema_ld": ("AR-CONT-01", "AR-COMM-04"),
    "llms_txt": ("AR-DISC-03", "AR-DISC-04", "AR-CONT-02", "AR-CONT-03"),
    "ssr": ("AR-CONT-01", "AR-CONT-02"),
    "machine_surfaces": (
        "AR-DISC-05",
        "AR-CAPA-01",
        "AR-CAPA-02",
        "AR-CAPA-04",
        "AR-CAPA-05",
        "AR-CAPA-06",
        "AR-CAPA-07",
        "AR-CAPA-08",
        "AR-CAPA-09",
        "AR-IDEN-02",
        "AR-IDEN-03",
        "AR-IDEN-04",
        "AR-IDEN-06",
        "AR-COMM-01",
        "AR-COMM-04",
        "AR-COMM-05",
    ),
    "pagespeed": ("AR-CONT-01",),
    "canonical": ("AR-DISC-01", "AR-DISC-02"),
    "indexing": ("AR-DISC-01",),
    "search_discovery": ("AR-DISC-02", "AR-DISC-06"),
}

# Locked advanced rows → AR family mapping (paid/private).
LOCKED_AR_CROSSWALK: dict[str, tuple[str, ...]] = {
    "earned_mentions_backlinks": ("AR-IDEN-01",),
    "owned_social_presence": ("AR-IDEN-01",),
    "social_traction_reviews": ("AR-IDEN-01",),
    "ai_citation_share": ("AR-CAPA-07",),
    "extraction_fidelity": ("AR-CONT-01",),
    "agent_task_simulation": ("AR-CAPA-04", "AR-COMM-02", "AR-COMM-04"),
    "multi_engine_index_coverage": ("AR-DISC-02",),
    "core_web_vitals": ("AR-CONT-01",),
    "keyword_competitor_gap": ("AR-DISC-06",),
}