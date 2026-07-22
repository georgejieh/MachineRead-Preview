"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import FindingsList from "@/components/findings-list";
import PresetPicker, { type PresetSelection } from "@/components/preset-picker";
import ScoreSummary from "@/components/score-summary";
import { PRESET_DISPLAY } from "@/constants/presets";
import { runAudit } from "@/lib/api";
import {
  ADVANCED_CHECK_ROW_COUNT,
  ESSENTIALS_CHECK_GROUP_COUNT,
  ESSENTIALS_CHECKED_POINT_MAX,
} from "@/lib/rubric";
import type { AuditResult, CheckResult, Preset } from "@/lib/types";

type Theme = "light" | "dark";

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}

function legacyScopeLabel(
  includeProtocols: boolean,
  includeAccountAuth: boolean,
  includeEcommerce: boolean,
): string {
  const baseLabel = includeEcommerce ? "Commerce storefront" : "General website";
  const optionalLabels: string[] = [];
  if (includeProtocols) {
    optionalLabels.push("API/protocol");
  }
  if (includeAccountAuth) {
    optionalLabels.push("account/auth");
  }
  return optionalLabels.length ? `${baseLabel} + ${optionalLabels.join(", ")}` : baseLabel;
}

/** Pretty label for the selected preset, used in the pre-scan header. */
function presetLabelFor(preset: Preset | null): string {
  if (preset === null) return "General website (classic)";
  return PRESET_DISPLAY[preset].label;
}

function DashboardPreview() {
  return (
    <section className="preview-grid" aria-label="Audit preview">
      <div className="preview-panel preview-score">
        <p className="panel-kicker">Standby</p>
        <div className="preview-ring">
          <span>--</span>
        </div>
        <p className="preview-muted">100-point readiness rubric</p>
      </div>
      <div className="preview-panel preview-bars">
        {["Off-site presence", "AI access", "Search discovery"].map((label, index) => (
          <div className="preview-bar-row" key={label}>
            <span>{label}</span>
            <div className="preview-bar">
              <div style={{ width: `${64 - index * 13}%` }} />
            </div>
          </div>
        ))}
      </div>
      <div className="preview-panel preview-inventory">
        <p className="panel-kicker">What gets scanned</p>
        <div className="inventory-line">
          <span>Check groups</span>
          <strong>{ESSENTIALS_CHECK_GROUP_COUNT}</strong>
        </div>
        <div className="inventory-line">
          <span>Scored points</span>
          <strong>{ESSENTIALS_CHECKED_POINT_MAX}</strong>
        </div>
        <div className="inventory-line">
          <span>Advanced checks listed</span>
          <strong>{ADVANCED_CHECK_ROW_COUNT}</strong>
        </div>
        <div className="inventory-line">
          <span>Logins or paid APIs needed</span>
          <strong>0</strong>
        </div>
      </div>
    </section>
  );
}

function LoadingDashboard() {
  return (
    <section className="loading-dashboard" aria-label="Audit in progress">
      <div className="loading-heading">
        <div className="spinner" />
        <div>
          <p className="panel-kicker">Scan in progress</p>
          <h2>
            Checking crawler policy, bot fetch access, structured data, text access, freshness,
            and search discovery hints.
          </h2>
        </div>
      </div>
      <div className="skeleton-grid">
        <div className="skeleton-block tall" />
        <div className="skeleton-block" />
        <div className="skeleton-block" />
        <div className="skeleton-block wide" />
      </div>
    </section>
  );
}

function summarizeInventory(checks: CheckResult[]) {
  const locked = checks.filter((check) => check.state === "locked");
  const checked = checks.filter((check) => check.state !== "locked");
  const needsWork = checked.filter((check) => check.state !== "pass");
  const warnings = checked.filter((check) => check.state === "warn");

  return {
    checkedCount: checked.length,
    lockedCount: locked.length,
    needsWorkCount: needsWork.length,
    warningCount: warnings.length,
  };
}

export default function AuditClient() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AuditResult | null>(null);
  const [theme, setTheme] = useState<Theme>("dark");
  // Default to the Blog/Content preset so first-time visitors see a fully
  // scoped audit instead of an empty legacy fallback.
  const [selection, setSelection] = useState<PresetSelection>({
    preset: "blog",
    customOverrides: {},
  });
  // Legacy booleans only apply when the user picks "General website (classic)".
  // They are intentionally NOT synced with preset selection — the backend
  // derives the equivalent dimensions from the chosen preset.
  const [legacyProtocols, setLegacyProtocols] = useState(false);
  const [legacyAccountAuth, setLegacyAccountAuth] = useState(false);
  const [legacyEcommerce, setLegacyEcommerce] = useState(false);
  const urlInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const storedTheme = window.localStorage.getItem("machineread-theme");
    const preferredTheme = window.matchMedia("(prefers-color-scheme: light)").matches
      ? "light"
      : "dark";
    const nextTheme = storedTheme === "light" || storedTheme === "dark" ? storedTheme : preferredTheme;

    setTheme(nextTheme);
    applyTheme(nextTheme);
  }, []);

  const inventory = useMemo(
    () =>
      result
        ? summarizeInventory(result.checks)
        : { checkedCount: 0, lockedCount: 0, needsWorkCount: 0, warningCount: 0 },
    [result],
  );

  // Before a scan runs, the scope chip shows the classic boolean-derived
  // label (when no preset is selected) or the preset's display label. After
  // the scan, the backend-resolved scope label takes over.
  const isLegacySelection = selection.preset === null;
  const preScanScopeLabel = isLegacySelection
    ? legacyScopeLabel(legacyProtocols, legacyAccountAuth, legacyEcommerce)
    : presetLabelFor(selection.preset);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const submittedUrl = urlInputRef.current?.value.trim() ?? "";
    if (!submittedUrl) {
      setError("Enter a website URL to run the audit.");
      return;
    }

    setError(null);
    setResult(null);
    setLoading(true);

    try {
      // Legacy path keeps the three booleans verbatim. Preset path passes
      // the chosen preset and the Custom overrides through; the booleans
      // become secondary dimensions on the backend and are ignored when a
      // preset is provided.
      const includeProtocols = isLegacySelection ? legacyProtocols : false;
      const includeAccountAuth = isLegacySelection ? legacyAccountAuth : false;
      const includeEcommerce = isLegacySelection ? legacyEcommerce : false;
      const data = await runAudit(
        submittedUrl,
        includeProtocols,
        includeAccountAuth,
        includeEcommerce,
        {
          preset: selection.preset,
          customOverrides:
            selection.preset === "custom" && Object.keys(selection.customOverrides).length > 0
              ? selection.customOverrides
              : null,
        },
      );
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Audit failed. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  function toggleTheme() {
    const nextTheme = theme === "dark" ? "light" : "dark";

    setTheme(nextTheme);
    applyTheme(nextTheme);
    window.localStorage.setItem("machineread-theme", nextTheme);
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">MR</span>
          <div>
            <span className="brand-name">MachineRead</span>
            <span className="brand-subtitle">AI &amp; search readiness audit</span>
          </div>
        </div>
        <button
          className="theme-toggle"
          type="button"
          onClick={toggleTheme}
          aria-label="Toggle light and dark mode"
          aria-pressed={theme === "dark"}
        >
          <span className={theme === "light" ? "active" : ""}>Light</span>
          <span className={theme === "dark" ? "active" : ""}>Dark</span>
        </button>
      </header>

      <section className="command-panel">
        <div className="command-copy">
          <p className="panel-kicker">AI visibility audit</p>
          <h1>
            See your site the way <em>machines</em> do.
          </h1>
          <p>
            MachineRead probes the public signals that AI agents, retrieval systems, and search
            crawlers rely on — crawler policy, bot access, semantic HTML, structured data,
            text/Markdown access, freshness, and discovery hints. {ESSENTIALS_CHECK_GROUP_COUNT}{" "}
            check groups run on every scan; deeper checks are listed but never guessed at.
          </p>
        </div>
        <form className="audit-form" onSubmit={handleSubmit}>
          <label htmlFor="audit-url">Target URL</label>
          <div className="input-row">
            <input
              ref={urlInputRef}
              id="audit-url"
              type="text"
              inputMode="url"
              placeholder="https://example.com"
              required
            />
            <button type="submit" disabled={loading}>
              {loading ? "Scanning…" : "Run audit"}
            </button>
          </div>
          <PresetPicker selection={selection} onChange={setSelection}>
            {isLegacySelection && (
              <div className="scope-control-row" aria-label="Classic scan options">
                <div className="scope-toggle" aria-label="Site type">
                  <button
                    className={!legacyEcommerce ? "active" : ""}
                    type="button"
                    onClick={() => setLegacyEcommerce(false)}
                    aria-pressed={!legacyEcommerce}
                  >
                    General
                  </button>
                  <button
                    className={legacyEcommerce ? "active" : ""}
                    type="button"
                    onClick={() => setLegacyEcommerce(true)}
                    aria-pressed={legacyEcommerce}
                  >
                    Commerce
                  </button>
                </div>
                <div className="capability-toggle" aria-label="Optional surfaces to include">
                  <button
                    className={legacyProtocols ? "active" : ""}
                    type="button"
                    onClick={() => setLegacyProtocols(!legacyProtocols)}
                    aria-pressed={legacyProtocols}
                  >
                    API/protocol
                  </button>
                  <button
                    className={legacyAccountAuth ? "active" : ""}
                    type="button"
                    onClick={() => setLegacyAccountAuth(!legacyAccountAuth)}
                    aria-pressed={legacyAccountAuth}
                  >
                    Account/auth
                  </button>
                </div>
              </div>
            )}
          </PresetPicker>
          <div className="scope-strip" aria-label="Audit scope">
            <span>{result ? result.scope.label : preScanScopeLabel}</span>
            <span>
              {result
                ? `${inventory.checkedCount} check groups run`
                : `${ESSENTIALS_CHECK_GROUP_COUNT} check groups`}
            </span>
            <span>
              {result
                ? `${inventory.lockedCount} advanced checks listed`
                : `${ADVANCED_CHECK_ROW_COUNT} advanced checks listed`}
            </span>
            {result && (
              <span>
                {inventory.warningCount
                  ? `${inventory.warningCount} watch items`
                  : `${inventory.needsWorkCount} need work`}
              </span>
            )}
          </div>
        </form>
      </section>

      {error && (
        <div className="error-banner" role="alert">
          <strong>Audit failed</strong>
          <span>{error}</span>
        </div>
      )}

      {loading && <LoadingDashboard />}

      {!loading && !result && !error && <DashboardPreview />}

      {result && !loading && (
        <div className="report-stack">
          <ScoreSummary result={result} />
          <FindingsList checks={result.checks} scope={result.scope} />
        </div>
      )}
    </div>
  );
}
