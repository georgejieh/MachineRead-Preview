export type CheckState = "pass" | "partial" | "fail" | "warn" | "locked";
export type Effort = "low" | "medium" | "high";
export type Pillar = "off_site" | "scrapability" | "seo";
export type EvidenceLevel = "verified" | "inferred" | "unknown" | "not_applicable";
export type Preset =
  | "blog"
  | "corporate"
  | "services"
  | "ecommerce"
  | "news"
  | "saas"
  | "custom";
export type MachineSurfacesScope = "common-contextual" | "full";

export interface AuditRequest {
  url: string;
  include_protocols: boolean;
  include_account_auth: boolean;
  include_ecommerce: boolean;
  preset?: Preset | null;
  custom_overrides?: Record<string, boolean> | null;
}

export interface CheckResult {
  pillar: Pillar;
  check_name: string;
  label: string;
  state: CheckState;
  evidence_level: EvidenceLevel;
  available_in: string;
  score: number;
  max_score: number;
  finding: string;
  fix: string;
  effort: Effort;
}

export interface PillarScores {
  off_site: number;
  scrapability: number;
  seo: number;
}

export interface PillarMax {
  off_site: number;
  scrapability: number;
  seo: number;
}

export interface AuditScope {
  include_protocols: boolean;
  include_account_auth: boolean;
  include_ecommerce: boolean;
  label: string;
  included_optional_surfaces: string[];
  excluded_optional_surfaces: string[];
  preset_applied: Preset | null;
  overrides_applied: Record<string, boolean>;
  included_families: string[];
  excluded_families: string[];
  machine_surfaces_scope: MachineSurfacesScope;
}

export interface BenchmarkEntry {
  name: string;
  category: string;
  group: string;
  size: string;
  url: string;
  overall_score: number;
  free_evidence_score: number;
  checked_score: number;
  checked_max: number;
  agent_readiness_score: number;
  agent_readiness_earned: number;
  agent_readiness_max: number;
  pillar_scores: PillarScores;
}

export interface BenchmarkComparison {
  score: number;
  checked_score: number;
  checked_max: number;
  benchmark_count: number;
  median_score: number;
  percentile: number;
  position_label: string;
  nearest: BenchmarkEntry[];
  entries: BenchmarkEntry[];
  basis: string;
  snapshot_date: string;
  caveat: string;
}

export interface AgentBenchmarkComparison {
  score: number;
  earned: number;
  max: number;
  benchmark_count: number;
  median_score: number;
  percentile: number;
  position_label: string;
  nearest: BenchmarkEntry[];
  entries: BenchmarkEntry[];
  basis: string;
  snapshot_date: string;
  caveat: string;
}

export interface AgentReadinessSummary {
  score: number;
  earned: number;
  max: number;
  label: string;
  categories: AgentReadinessCategory[];
  passed: string[];
  missing: string[];
  not_checked: string[];
  benchmark: AgentBenchmarkComparison;
  caveat: string;
}

export interface AgentReadinessCategory {
  name: string;
  earned: number;
  max: number;
  score: number;
  passed: string[];
  missing: string[];
  excluded: string[];
}

export interface AuditResult {
  url: string;
  scope: AuditScope;
  overall_score: number;
  pillar_scores: PillarScores;
  pillar_max: PillarMax;
  agent_readiness: AgentReadinessSummary;
  benchmark: BenchmarkComparison;
  checks: CheckResult[];
}

export type SummaryEvidenceLevel =
  | "verified"
  | "inferred"
  | "sampled"
  | "unavailable";
export type SummaryState = "fail" | "partial" | "warn";
export type SummaryLimitationCode =
  | "relative_scores"
  | "no_live_ranking"
  | "no_provider_ip_auth"
  | "no_paid_crawlers";

export interface SummaryScope {
  preset: string | null;
  protocols: boolean;
  account_auth: boolean;
  ecommerce: boolean;
  overrides: Record<string, boolean>;
}

export interface SummaryScorePair {
  earned: number;
  max: number;
}

export interface SummaryPercent extends SummaryScorePair {
  percent: number;
}

export interface SummaryScores {
  overall: SummaryScorePair;
  pillars: Record<Pillar, SummaryScorePair>;
  essentials: SummaryPercent;
  agent_readiness: SummaryPercent;
}

export interface SummaryBenchmark {
  percentile: number;
  median_percent: number;
  peer_count: number;
  snapshot: string;
}

export interface SummaryCheckCounts {
  included: number;
  locked: number;
  pass: number;
  partial: number;
  fail: number;
  warn: number;
  attention_total: number;
}

export interface SummaryAttentionItem {
  check_name: string;
  pillar: Pillar;
  state: SummaryState;
  evidence_level: SummaryEvidenceLevel;
  earned: number;
  max: number;
  effort: Effort;
}

export interface AuditSummary {
  api_version: "1.0";
  summary_version: "1.0";
  url: string;
  scope: SummaryScope;
  scores: SummaryScores;
  benchmarks: Record<"essentials" | "agent_readiness", SummaryBenchmark>;
  checks: SummaryCheckCounts;
  attention: SummaryAttentionItem[];
  limitations: [
    "relative_scores",
    "no_live_ranking",
    "no_provider_ip_auth",
    "no_paid_crawlers",
  ];
}