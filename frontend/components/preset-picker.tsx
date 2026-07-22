"use client";

import type { ReactNode } from "react";
import CustomOverridesPanel from "@/components/custom-overrides-panel";
import { DEFAULT_CUSTOM_OVERRIDES, NAMED_PRESETS, PRESET_DISPLAY } from "@/constants/presets";
import type { Preset } from "@/lib/types";

/**
 * Selection state held by `audit-client.tsx`. The picker distinguishes
 * the explicit "classic general website" path (preset === null) from the
 * seven named presets. Custom overrides only apply when preset === "custom".
 */
export interface PresetSelection {
  preset: Preset | null;
  customOverrides: Record<string, boolean>;
}

interface Props {
  selection: PresetSelection;
  onChange: (next: PresetSelection) => void;
  /**
   * Extra controls rendered inside the "Classic mode" disclosure — the
   * legacy scope toggles owned by `audit-client.tsx`. Keeping them as
   * children keeps the booleans' state where the submit handler lives.
   */
  children?: ReactNode;
}

/** Short chip label per preset (full label stays on the detail line). */
const CHIP_LABELS: Record<Preset, string> = {
  blog: "Blog / Content",
  corporate: "Corporate / Brand",
  services: "Services / Local",
  ecommerce: "Ecommerce",
  news: "News / Publisher",
  saas: "SaaS / API",
  custom: "Custom",
};

/**
 * PresetPicker
 *
 * Compact chip-based preset selector. One row of site-profile chips, a
 * single detail line describing the active choice, and a "Classic mode"
 * disclosure for reproducing older audits. The picker never validates
 * preset compatibility — the backend (`backend/app/presets.py`) rejects
 * impossible combinations at the API boundary.
 */
export default function PresetPicker({ selection, onChange, children }: Props) {
  const { preset, customOverrides } = selection;

  function selectPreset(next: Preset) {
    // Seed the panel so the user sees the Blog baseline, not an empty scope.
    if (next === "custom") {
      onChange({ preset: "custom", customOverrides: { ...DEFAULT_CUSTOM_OVERRIDES } });
    } else {
      onChange({ preset: next, customOverrides: {} });
    }
  }

  function selectLegacy() {
    onChange({ preset: null, customOverrides: {} });
  }

  const activeEntry = preset ? PRESET_DISPLAY[preset] : null;

  return (
    <div className="preset-picker" aria-label="Site profile selector">
      <div className="preset-picker-heading">
        <p className="panel-kicker">Site profile</p>
        <p className="preset-picker-subtitle">
          Pick the closest match — it decides which checks apply to your site.
        </p>
      </div>

      <div className="preset-chip-row" role="radiogroup" aria-label="Site profile">
        {NAMED_PRESETS.map((key) => (
          <button
            key={key}
            type="button"
            role="radio"
            aria-checked={preset === key}
            className={`preset-chip${preset === key ? " active" : ""}`}
            onClick={() => selectPreset(key)}
          >
            {CHIP_LABELS[key]}
          </button>
        ))}
        <button
          type="button"
          role="radio"
          aria-checked={preset === "custom"}
          className={`preset-chip preset-chip-custom${preset === "custom" ? " active" : ""}`}
          onClick={() => selectPreset("custom")}
        >
          {CHIP_LABELS.custom}
        </button>
      </div>

      {activeEntry ? (
        <p className="preset-chip-note">
          <strong>{activeEntry.label}</strong>
          <span>
            {activeEntry.description}
            {activeEntry.families.length
              ? ` Adds: ${activeEntry.families.join(", ")}.`
              : " Runs the universal core checks only."}
          </span>
        </p>
      ) : (
        <p className="preset-chip-note">
          <strong>Classic mode</strong>
          <span>Reproduces earlier audits using the original scope toggles below.</span>
        </p>
      )}

      {preset === "custom" && (
        <CustomOverridesPanel
          overrides={customOverrides}
          onChange={(next) => onChange({ preset: "custom", customOverrides: next })}
        />
      )}

      <details className="preset-advanced" open={preset === null}>
        <summary>Classic mode</summary>
        <button
          type="button"
          role="radio"
          aria-checked={preset === null}
          className={`preset-legacy-button${preset === null ? " active" : ""}`}
          onClick={selectLegacy}
        >
          <span className="preset-legacy-title">General website (classic)</span>
          <span className="preset-legacy-description">
            The original audit scope with simple site-type toggles. Pick this only if you are
            reproducing an earlier audit.
          </span>
        </button>
        {children}
      </details>
    </div>
  );
}
