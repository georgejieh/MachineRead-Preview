"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  PolarAngleAxis,
  RadialBar,
  RadialBarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ESSENTIALS_CHECK_GROUP_COUNT } from "@/lib/rubric";
import { defaultBenchmarkGroup } from "@/lib/benchmark-defaults";
import type { AuditResult, BenchmarkEntry, CheckResult, Pillar } from "@/lib/types";

const PILLAR_LABELS: Record<Pillar, string> = {
  off_site: "Off-site presence",
  scrapability: "AI access",
  seo: "Search discovery",
};

const PILLAR_ORDER: Pillar[] = ["off_site", "scrapability", "seo"];

const STATE_LABELS: Record<string, string> = {
  pass: "Passed",
  partial: "Partial",
  fail: "Failed",
  warn: "Watch",
  locked: "Advanced",
};

const GROUP_LABELS: Record<string, string> = {
  blog: "Blog",
  corporate: "Corporate",
  ecommerce: "Ecommerce",
  news: "News",
  saas: "SaaS",
  services: "Services",
};

const SIZE_LABELS: Record<string, string> = {
  platform: "Platform",
  enterprise: "Enterprise",
  specialty: "Specialty",
  boutique: "Boutique",
};

type FocusPanel = "total" | "seo" | "ai";
type BenchmarkMode = "essentials" | "agent";

interface ScoreRingProps {
  score: number;
  max: number;
}

function ScoreRing({ score, max }: ScoreRingProps) {
  const percent = Math.round((score / max) * 100);
  const data = [{ value: percent, fill: "var(--accent)" }];

  return (
    <div className="score-ring" aria-label={`Score ${score} out of ${max}`}>
      <ResponsiveContainer width="100%" height="100%">
        <RadialBarChart
          cx="50%"
          cy="50%"
          innerRadius="76%"
          outerRadius="100%"
          data={data}
          startAngle={90}
          endAngle={-270}
        >
          <PolarAngleAxis type="number" domain={[0, 100]} tick={false} />
          <RadialBar dataKey="value" cornerRadius={6} background={{ fill: "var(--chart-track)" }} />
        </RadialBarChart>
      </ResponsiveContainer>
      <div className="score-ring-label">
        <strong>{score}</strong>
        <span>/ {max}</span>
      </div>
    </div>
  );
}

function scoreBand(score: number): string {
  if (score >= 85) return "Elite";
  if (score >= 70) return "Strong";
  if (score >= 50) return "Developing";
  return "At risk";
}

function scorePercent(score: number, max: number): number {
  if (!max) return 0;
  return Math.round((score / max) * 100);
}

function lockedMax(checks: CheckResult[]): number {
  return checks
    .filter((check) => check.state === "locked")
    .reduce((total, check) => total + check.max_score, 0);
}

function checkedPillarMax(checks: CheckResult[], pillar: Pillar): number {
  return checks
    .filter((check) => check.state !== "locked" && check.pillar === pillar)
    .reduce((total, check) => total + check.max_score, 0);
}

function checkedPillarScore(checks: CheckResult[], pillar: Pillar): number {
  return checks
    .filter((check) => check.state !== "locked" && check.pillar === pillar)
    .reduce((total, check) => total + check.score, 0);
}

function stateCounts(checks: CheckResult[]) {
  return checks.reduce<Record<string, number>>((counts, check) => {
    counts[check.state] = (counts[check.state] ?? 0) + 1;
    return counts;
  }, {});
}

function topActions(checks: CheckResult[], pillar?: Pillar): CheckResult[] {
  return checks
    .filter((check) => check.state !== "pass" && check.state !== "locked")
    .filter((check) => !pillar || check.pillar === pillar)
    .sort((a, b) => b.max_score - b.score - (a.max_score - a.score))
    .slice(0, 4);
}

function benchmarkWidth(score: number): string {
  return `${Math.max(0, Math.min(100, score))}%`;
}

function scoreForEntry(entry: BenchmarkEntry, mode: BenchmarkMode): number {
  return mode === "agent" ? entry.agent_readiness_score : entry.free_evidence_score;
}

function nearestBenchmarkSummary(entries: BenchmarkEntry[], mode: BenchmarkMode): string {
  return entries.map((entry) => `${entry.name} ${scoreForEntry(entry, mode)}/100`).join(", ");
}

function fullPillarSummary(result: AuditResult): string {
  return PILLAR_ORDER.map(
    (pillar) => `${PILLAR_LABELS[pillar]} ${result.pillar_scores[pillar]}/${result.pillar_max[pillar]}`,
  ).join(" / ");
}

interface SummaryTileProps {
  active: boolean;
  detail: string;
  label: string;
  score: number;
  onSelect: () => void;
}

function SummaryTile({ active, detail, label, score, onSelect }: SummaryTileProps) {
  return (
    <button
      className={active ? "summary-tile active" : "summary-tile"}
      type="button"
      onClick={onSelect}
      aria-pressed={active}
    >
      <span>{label}</span>
      <strong>{score}</strong>
      <p>{detail}</p>
    </button>
  );
}

interface ActionListProps {
  actions: CheckResult[];
}

function ActionList({ actions }: ActionListProps) {
  if (!actions.length) {
    return <div className="empty-state">No included action items in this view.</div>;
  }

  return (
    <div className="action-list">
      {actions.map((check) => (
        <article className={`action-row state-${check.state}`} key={check.check_name}>
          <span className="state-badge">{STATE_LABELS[check.state]}</span>
          <div>
            <strong>{check.label}</strong>
            <p>{check.fix}</p>
          </div>
          <span className="action-points">{check.max_score - check.score} pts</span>
        </article>
      ))}
    </div>
  );
}

interface BenchmarkExplorerProps {
  result: AuditResult;
}

function BenchmarkExplorer({ result }: BenchmarkExplorerProps) {
  const [mode, setMode] = useState<BenchmarkMode>("essentials");
  const entries = mode === "agent" ? result.agent_readiness.benchmark.entries : result.benchmark.entries;
  const nearestUrls = new Set(
    mode === "agent"
      ? result.agent_readiness.benchmark.nearest.map((entry) => entry.url)
      : result.benchmark.nearest.map((entry) => entry.url),
  );
  const groupOptions = useMemo(
    () => ["all", ...Array.from(new Set(entries.map((entry) => entry.group))).sort()],
    [entries],
  );
  const availableGroups = useMemo(
    () => Array.from(new Set(entries.map((entry) => entry.group))),
    [entries],
  );
  const [group, setGroup] = useState(() =>
    defaultBenchmarkGroup(result.scope.preset_applied, availableGroups),
  );
  useEffect(() => {
    if (group !== "all" && !availableGroups.includes(group)) {
      setGroup("all");
    }
  }, [group, availableGroups]);
  const [size, setSize] = useState("all");
  const sizeOptions = useMemo(
    () => ["all", ...Array.from(new Set(entries.map((entry) => entry.size))).sort()],
    [entries],
  );
  const filteredEntries = entries.filter((entry) => {
    const groupMatches = group === "all" || entry.group === group;
    const sizeMatches = size === "all" || entry.size === size;
    return groupMatches && sizeMatches;
  });
  const userScore = mode === "agent" ? result.agent_readiness.score : result.benchmark.score;
  const comparison = mode === "agent" ? result.agent_readiness.benchmark : result.benchmark;

  return (
    <section className="panel benchmark-panel">
      <div className="panel-heading benchmark-heading">
        <div>
          <p className="panel-kicker">Benchmarks</p>
          <h3>{comparison.position_label}</h3>
        </div>
        <div className="benchmark-rank">
          <span>Position</span>
          <strong>{comparison.percentile}%</strong>
        </div>
      </div>

      <div className="benchmark-controls">
        <div className="filter-group" aria-label="Benchmark score mode">
          <button
            className={mode === "essentials" ? "filter-button active" : "filter-button"}
            type="button"
            onClick={() => setMode("essentials")}
          >
            Essentials
          </button>
          <button
            className={mode === "agent" ? "filter-button active" : "filter-button"}
            type="button"
            onClick={() => setMode("agent")}
          >
            Agent readiness
          </button>
        </div>
        <label>
          Segment
          <select value={group} onChange={(event) => setGroup(event.target.value)}>
            {groupOptions.map((option) => (
              <option key={option} value={option}>
                {option === "all" ? "All segments" : GROUP_LABELS[option] ?? option}
              </option>
            ))}
          </select>
        </label>
        <label>
          Size
          <select value={size} onChange={(event) => setSize(event.target.value)}>
            {sizeOptions.map((option) => (
              <option key={option} value={option}>
                {option === "all" ? "All sizes" : SIZE_LABELS[option] ?? option}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="benchmark-copy compact">
        <p>
          {comparison.basis}. Compared with {filteredEntries.length} visible of{" "}
          {comparison.benchmark_count} benchmark sites from the {comparison.snapshot_date} snapshot.
        </p>
        <p>
          Median benchmark score is {comparison.median_score}/100. {comparison.caveat}
        </p>
      </div>

      <div className="benchmark-list" aria-label="Benchmark comparison scores">
        <div className="benchmark-row benchmark-user">
          <div>
            <strong>Your site</strong>
            <span>{mode === "agent" ? result.agent_readiness.label : result.scope.label}</span>
          </div>
          <div className="benchmark-bar" aria-hidden="true">
            <div style={{ width: benchmarkWidth(userScore) }} />
          </div>
          <strong>{userScore}</strong>
        </div>
        {filteredEntries.map((entry) => (
          <div
            className={`benchmark-row ${nearestUrls.has(entry.url) ? "benchmark-nearest" : ""}`}
            key={entry.url}
          >
            <div>
              <strong>{entry.name}</strong>
              <span>
                {GROUP_LABELS[entry.group] ?? entry.group} / {SIZE_LABELS[entry.size] ?? entry.size}
              </span>
            </div>
            <div className="benchmark-bar" aria-hidden="true">
              <div style={{ width: benchmarkWidth(scoreForEntry(entry, mode)) }} />
            </div>
            <strong>{scoreForEntry(entry, mode)}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}

interface Props {
  result: AuditResult;
}

export default function ScoreSummary({ result }: Props) {
  const [activePanel, setActivePanel] = useState<FocusPanel>("total");
  const evidenceScore = result.benchmark.score;
  const checkedPointsEarned = result.benchmark.checked_score;
  const checkedPoints = result.benchmark.checked_max;
  const includedCheckGroups = result.checks.filter((check) => check.state !== "locked").length;
  const advancedPoints = lockedMax(result.checks);
  const seoScore = checkedPillarScore(result.checks, "seo");
  const seoMax = checkedPillarMax(result.checks, "seo");
  const seoPercent = scorePercent(seoScore, seoMax);
  const aiScore = checkedPillarScore(result.checks, "scrapability");
  const aiMax = checkedPillarMax(result.checks, "scrapability");
  const counts = stateCounts(result.checks);
  const pillarData = PILLAR_ORDER.map((pillar) => {
    const score = checkedPillarScore(result.checks, pillar);
    const max = checkedPillarMax(result.checks, pillar);
    const percent = scorePercent(score, max);
    return {
      name: PILLAR_LABELS[pillar],
      shortName: pillar === "off_site" ? "Off-site" : pillar === "scrapability" ? "AI access" : "Search",
      score,
      max,
      percent,
      remaining: 100 - percent,
    };
  });
  const activeActions =
    activePanel === "seo"
      ? topActions(result.checks, "seo")
      : activePanel === "ai"
        ? topActions(result.checks, "scrapability")
        : topActions(result.checks);
  const activeSummary =
    activePanel === "seo"
      ? `Search discovery is ${seoPercent}/100 across included crawl access, sitemap, canonical, indexing directive, metadata, freshness, hreflang, and sampled-page signals. Live index coverage, rankings, and field Core Web Vitals remain advanced verification.`
      : activePanel === "ai"
        ? `AI access combines ${aiScore}/${aiMax} included points from crawler policy, bot fetches, HTML readability, structured data, text/Markdown access, and protocol discovery with ${result.agent_readiness.earned}/${result.agent_readiness.max} explicit agent-native signals. ${result.agent_readiness.benchmark.position_label}.`
        : `${result.url} has a ${evidenceScore}/100 Essentials evidence score from bounded public HTTP, DNS, and page checks across ${includedCheckGroups} check groups (${checkedPointsEarned}/${checkedPoints} checked points). The full rubric score is ${result.overall_score}/100 with ${advancedPoints} advanced points left unscored until verified.`;

  return (
    <section className="report-overview">
      <div className="summary-grid" aria-label="Score summary">
        <SummaryTile
          active={activePanel === "total"}
          detail={`${includedCheckGroups}/${ESSENTIALS_CHECK_GROUP_COUNT} groups, ${checkedPointsEarned}/${checkedPoints} pts`}
          label="Total Essentials Score"
          score={evidenceScore}
          onSelect={() => setActivePanel("total")}
        />
        <SummaryTile
          active={activePanel === "seo"}
          detail={`${seoScore}/${seoMax} included SEO points`}
          label="Search Discovery Score"
          score={seoPercent}
          onSelect={() => setActivePanel("seo")}
        />
        <SummaryTile
          active={activePanel === "ai"}
          detail={`${result.agent_readiness.score}/100 agent-native lens`}
          label="AI Readiness Score"
          score={result.agent_readiness.score}
          onSelect={() => setActivePanel("ai")}
        />
      </div>

      <section className="panel focus-panel">
        <div className="focus-score">
          <div>
            <p className="panel-kicker">
              {activePanel === "total"
                ? "Total score"
                : activePanel === "seo"
                  ? "Search discovery"
                  : "AI readiness"}
            </p>
            <h2>{scoreBand(activePanel === "seo" ? seoPercent : activePanel === "ai" ? result.agent_readiness.score : evidenceScore)}</h2>
            <p>{activeSummary}</p>
          </div>
          <ScoreRing
            score={activePanel === "seo" ? seoPercent : activePanel === "ai" ? result.agent_readiness.score : evidenceScore}
            max={100}
          />
        </div>

        <div className="focus-detail-grid">
          <div className="detail-metrics">
            <div>
              <span>Full rubric</span>
              <strong>{result.overall_score}/100</strong>
            </div>
            <div>
              <span>Advanced points</span>
              <strong>{advancedPoints}</strong>
            </div>
            <div>
              <span>Full pillar scores</span>
              <strong>{fullPillarSummary(result)}</strong>
            </div>
            <div>
              <span>Benchmark median</span>
              <strong>{result.benchmark.median_score}/100</strong>
            </div>
            <div>
              <span>Scope</span>
              <strong>{result.scope.label}</strong>
            </div>
            <div>
              <span>Nearest peers</span>
              <strong>{nearestBenchmarkSummary(result.benchmark.nearest, "essentials")}</strong>
            </div>
          </div>

          <div className="pillar-chart compact-chart">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={pillarData} layout="vertical" margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
                <XAxis type="number" domain={[0, 100]} hide />
                <YAxis dataKey="shortName" type="category" width={76} tickLine={false} axisLine={false} />
                <Tooltip
                  cursor={{ fill: "var(--chart-hover)" }}
                  contentStyle={{
                    background: "var(--surface-elevated)",
                    border: "1px solid var(--border)",
                    color: "var(--text)",
                  }}
                  formatter={(value, name) => [
                    `${value}${name === "percent" || name === "remaining" ? "%" : ""}`,
                    name === "percent" ? "Included evidence" : "Gap",
                  ]}
                />
                <Bar dataKey="percent" stackId="score" fill="var(--accent)" radius={[2, 0, 0, 2]} />
                <Bar dataKey="remaining" stackId="score" fill="var(--chart-track)" radius={[0, 2, 2, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {activePanel === "ai" && (
          <>
            <div className="agent-category-grid">
              {result.agent_readiness.categories.map((category) => (
                <article className="agent-category-card" key={category.name}>
                  <span>{category.name}</span>
                  <strong>{category.max ? `${category.score}/100` : "Scoped out"}</strong>
                  <p>
                    {category.max
                      ? `${category.earned}/${category.max} included signals present`
                      : `${category.excluded.length} signals excluded as not relevant`}
                  </p>
                </article>
              ))}
            </div>
            <div className="agent-signal-grid compact-signals">
              <div className="agent-signal-list">
                <span>Signals found</span>
                {result.agent_readiness.passed.length ? (
                  result.agent_readiness.passed.map((signal) => <strong key={signal}>{signal}</strong>)
                ) : (
                  <strong>No explicit agent-native signals found</strong>
                )}
              </div>
              <div className="agent-signal-list missing">
                <span>Missing included surfaces</span>
                {result.agent_readiness.missing.slice(0, 6).map((signal) => (
                  <strong key={signal}>{signal}</strong>
                ))}
                {result.agent_readiness.missing.length > 6 && (
                  <strong>{result.agent_readiness.missing.length - 6} more missing signals</strong>
                )}
              </div>
              <div className="agent-signal-list notes">
                <span>Not checked in this audit</span>
                {result.agent_readiness.not_checked.map((note) => (
                  <strong key={note}>{note}</strong>
                ))}
                <p>{result.agent_readiness.caveat}</p>
              </div>
            </div>
          </>
        )}

        <div className="scope-note-list">
          {result.scope.included_optional_surfaces.map((surface) => (
            <span key={`included-${surface}`}>{surface} included by scope</span>
          ))}
          {result.scope.excluded_optional_surfaces.map((surface) => (
            <span key={`excluded-${surface}`}>{surface} excluded by scope</span>
          ))}
          {Object.entries(STATE_LABELS).map(([state, label]) => (
            <span key={state}>
              {label}: {counts[state] ?? 0}
            </span>
          ))}
        </div>

        <div className="panel-heading action-heading">
          <div>
            <p className="panel-kicker">Action items</p>
            <h3>{activePanel === "total" ? "Highest-impact included fixes" : "Focused fixes"}</h3>
          </div>
        </div>
        <ActionList actions={activeActions} />
      </section>

      <BenchmarkExplorer
        key={`${result.url}::${result.scope.preset_applied ?? "all"}`}
        result={result}
      />
    </section>
  );
}
