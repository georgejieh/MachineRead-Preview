"use client";

import { useMemo, useState } from "react";
import { PRESET_DISPLAY } from "@/constants/presets";
import type { AuditScope, CheckResult, CheckState, Pillar } from "@/lib/types";

const PILLAR_LABELS: Record<Pillar, string> = {
  off_site: "Off-site",
  scrapability: "Agent UX",
  seo: "Search",
};

const PILLAR_ORDER = ["all", "scrapability", "off_site", "seo"] as const;
type PillarFilter = (typeof PILLAR_ORDER)[number];

const STATE_LABELS: Record<CheckState, string> = {
  pass: "Pass",
  partial: "Partial",
  fail: "Fail",
  warn: "Watch",
  locked: "Advanced",
};

const STATUS_ORDER = ["all", "needs_work", "pass", "locked"] as const;
type StatusFilter = (typeof STATUS_ORDER)[number];

const STATUS_LABELS: Record<StatusFilter, string> = {
  all: "All",
  needs_work: "Needs work",
  pass: "Passing",
  locked: "Advanced",
};

const EFFORT_LABELS: Record<string, string> = {
  low: "Quick win",
  medium: "Moderate",
  high: "Heavy lift",
};

const EVIDENCE_LABELS: Record<string, string> = {
  verified: "Verified",
  inferred: "Inferred",
  unknown: "Unknown",
  not_applicable: "Advanced",
};

interface FilterButtonProps<T extends string> {
  label: string;
  value: T;
  selected: T;
  onSelect: (value: T) => void;
}

function FilterButton<T extends string>({ label, value, selected, onSelect }: FilterButtonProps<T>) {
  return (
    <button
      className={selected === value ? "filter-button active" : "filter-button"}
      type="button"
      onClick={() => onSelect(value)}
    >
      {label}
    </button>
  );
}

function statusMatches(check: CheckResult, status: StatusFilter): boolean {
  if (status === "all") return true;
  if (status === "needs_work") return check.state !== "pass" && check.state !== "locked";
  return check.state === status;
}

function sortChecks(checks: CheckResult[]) {
  const stateWeight: Record<CheckState, number> = {
    fail: 0,
    partial: 1,
    warn: 2,
    locked: 3,
    pass: 4,
  };

  return [...checks].sort((a, b) => {
    const stateDelta = stateWeight[a.state] - stateWeight[b.state];
    if (stateDelta !== 0) return stateDelta;
    return b.max_score - a.max_score;
  });
}

interface FindingRowProps {
  check: CheckResult;
}

function FindingRow({ check }: FindingRowProps) {
  const [open, setOpen] = useState(false);
  const gap = check.max_score - check.score;
  const isLocked = check.state === "locked";

  return (
    <article className={`finding-row state-${check.state}`}>
      <button className="finding-trigger" type="button" onClick={() => setOpen(!open)}>
        <span className="state-badge">{STATE_LABELS[check.state]}</span>
        <span className="finding-title">
          <strong>{check.label}</strong>
          <span>
            {PILLAR_LABELS[check.pillar]} / {check.check_name}
          </span>
        </span>
        <span className="finding-score">
          {check.score}/{check.max_score}
        </span>
        <span className="finding-tier">{check.available_in}</span>
        <span className="row-caret" aria-hidden="true">
          {open ? "Hide" : "View"}
        </span>
      </button>

      {open && (
        <div className="finding-detail">
          <div>
            <p className="detail-label">Finding</p>
            <p>{check.finding}</p>
          </div>
          <div>
            <p className="detail-label">{isLocked ? "Advanced coverage" : "Recommended action"}</p>
            <p>{check.fix}</p>
          </div>
          <div className="finding-meta">
            <span>{EFFORT_LABELS[check.effort]}</span>
            <span>{EVIDENCE_LABELS[check.evidence_level]}</span>
            {gap > 0 && <span>{gap} points available</span>}
          </div>
        </div>
      )}
    </article>
  );
}

interface SectionProps {
  checks: CheckResult[];
  title: string;
  description: string;
}

function FindingsSection({ checks, title, description }: SectionProps) {
  return (
    <section className="findings-section">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">{checks.length} rows</p>
          <h3>{title}</h3>
        </div>
        <p>{description}</p>
      </div>
      {checks.length ? (
        <div className="finding-table">
          {checks.map((check) => (
            <FindingRow key={check.check_name} check={check} />
          ))}
        </div>
      ) : (
        <div className="empty-state">No rows match the current filters.</div>
      )}
    </section>
  );
}

interface Props {
  checks: CheckResult[];
  scope: AuditScope;
}

const SCOPE_SCOPE_LABELS: Record<string, string> = {
  "common-contextual": "Common contextual surfaces",
  full: "Full protocol scope",
};

function presetLabelForScope(scope: AuditScope): string {
  return scope.preset_applied ? PRESET_DISPLAY[scope.preset_applied].label : "General website (classic)";
}

export default function FindingsList({ checks, scope }: Props) {
  const [pillar, setPillar] = useState<PillarFilter>("all");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [showDetails, setShowDetails] = useState(false);

  const filteredChecks = useMemo(
    () =>
      sortChecks(
        checks.filter((check) => {
          const pillarMatches = pillar === "all" || check.pillar === pillar;
          return pillarMatches && statusMatches(check, status);
        }),
      ),
    [checks, pillar, status],
  );
  const includedChecks = filteredChecks.filter((check) => check.state !== "locked");
  const lockedChecks = filteredChecks.filter((check) => check.state === "locked");
  const actionCount = checks.filter((check) => check.state !== "pass" && check.state !== "locked").length;

  return (
    <section className="findings-panel">
      <div className="findings-header">
        <div>
          <p className="panel-kicker">Audit inventory</p>
          <h2>Checked signals and advanced coverage</h2>
        </div>
        <p>
          Every result below comes from bounded public checks — crawler policy files, live bot
          fetches, page markup, and discovery endpoints. Signals we can only infer are labelled as
          proxies, and checks that need provider data or deeper crawling stay in the Advanced list
          rather than being guessed at. Each row carries its own caveats.
        </p>
        <button
          className="secondary-action"
          type="button"
          onClick={() => setShowDetails(!showDetails)}
          aria-expanded={showDetails}
        >
          {showDetails ? "Hide details" : "Show all rows"}
        </button>
      </div>

      <div className="scope-summary" aria-label="Resolved audit scope">
        <div className="scope-summary-row">
          <span className="scope-summary-kicker">Preset applied</span>
          <strong>{presetLabelForScope(scope)}</strong>
          <span className="scope-summary-meta">
            {SCOPE_SCOPE_LABELS[scope.machine_surfaces_scope] ?? scope.machine_surfaces_scope}
          </span>
        </div>
        <div className="scope-summary-families">
          <div className="scope-summary-family-block">
            <span className="scope-summary-kicker">Included families</span>
            {scope.included_families.length ? (
              <ul>
                {scope.included_families.map((family) => (
                  <li key={`inc-${family}`}>{family}</li>
                ))}
              </ul>
            ) : (
              <p className="scope-summary-empty">None — only the universal core is in scope.</p>
            )}
          </div>
          <div className="scope-summary-family-block">
            <span className="scope-summary-kicker">Excluded families</span>
            {scope.excluded_families.length ? (
              <ul>
                {scope.excluded_families.map((family) => (
                  <li key={`exc-${family}`}>{family}</li>
                ))}
              </ul>
            ) : (
              <p className="scope-summary-empty">None — every supported family is in scope.</p>
            )}
          </div>
        </div>
      </div>

      {!showDetails ? (
        <div className="inventory-summary">
          <strong>{actionCount} included check groups need attention or caveat review.</strong>
          <span>{lockedChecks.length} advanced rows are available for deeper coverage.</span>
        </div>
      ) : (
        <>
          <div className="filter-grid">
            <div className="filter-group" aria-label="Pillar filter">
              {PILLAR_ORDER.map((item) => (
                <FilterButton
                  key={item}
                  label={item === "all" ? "All pillars" : PILLAR_LABELS[item]}
                  value={item}
                  selected={pillar}
                  onSelect={setPillar}
                />
              ))}
            </div>
            <div className="filter-group" aria-label="Status filter">
              {STATUS_ORDER.map((item) => (
                <FilterButton
                  key={item}
                  label={STATUS_LABELS[item]}
                  value={item}
                  selected={status}
                  onSelect={setStatus}
                />
              ))}
            </div>
          </div>

          <FindingsSection
            checks={includedChecks}
            title="Included in Essentials"
            description="These results come from bounded crawling, page parsing, discovery files, public entity lookups, and clearly labelled proxy evidence."
          />
          <FindingsSection
            checks={lockedChecks}
            title="Available with advanced coverage"
            description="These rows require external data, deeper crawling, authenticated sources, or agent task simulation before they can be scored."
          />
        </>
      )}
    </section>
  );
}
