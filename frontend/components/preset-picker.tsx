"use client";

import CustomOverridesPanel from "@/components/custom-overrides-panel";
import { DEFAULT_CUSTOM_OVERRIDES, NAMED_PRESETS, PRESET_DISPLAY } from "@/constants/presets";
import type { Preset } from "@/lib/types";

/**
 * Selection state held by `audit-client.tsx`. The picker distinguishes
 * the explicit "legacy general website" path (preset === null) from the
 * seven named presets. Power-user overrides only apply when
 * preset === "custom".
 */
export interface PresetSelection {
  preset: Preset | null;
  customOverrides: Record<string, boolean>;
}

interface Props {
  selection: PresetSelection;
  onChange: (next: PresetSelection) => void;
}

/**
 * PresetPicker
 *
 * Card-based preset selector that replaces the legacy three-boolean scope
 * row. The picker exposes six named presets + one Custom card + a separate
 * "General website (legacy)" fallback so existing flows keep working
 * unchanged. The picker itself never validates preset compatibility — it
 * just mirrors the preset catalog for display and lets the backend
 * (`backend/app/presets.py`) reject impossible combinations at the API
 * boundary.
 */
export default function PresetPicker({ selection, onChange }: Props) {
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

  return (
    <div className="preset-picker" aria-label="Audit preset selector">
      <div className="preset-picker-heading">
        <p className="panel-kicker">Step 1 — pick a website category</p>
        <h3>Choose a preset</h3>
        <p className="preset-picker-subtitle">
          Each preset declares its universal core plus its per-preset check families before
          the audit starts. The Custom card unlocks the Power User overrides panel below.
        </p>
      </div>

      <div className="preset-card-grid" role="radiogroup" aria-label="Website category preset">
        {NAMED_PRESETS.map((key) => {
          const entry = PRESET_DISPLAY[key];
          const selected = preset === key;
          return (
            <button
              key={key}
              type="button"
              role="radio"
              aria-checked={selected}
              className={`preset-card${selected ? " active" : ""}`}
              onClick={() => selectPreset(key)}
            >
              <span className="preset-card-kicker">{entry.familyCount} families</span>
              <span className="preset-card-title">{entry.label}</span>
              <span className="preset-card-description">{entry.description}</span>
            </button>
          );
        })}

        <button
          type="button"
          role="radio"
          aria-checked={preset === "custom"}
          className={`preset-card preset-card-custom${preset === "custom" ? " active" : ""}`}
          onClick={() => selectPreset("custom")}
        >
          <span className="preset-card-kicker">Power user</span>
          <span className="preset-card-title">{PRESET_DISPLAY.custom.label}</span>
          <span className="preset-card-description">{PRESET_DISPLAY.custom.description}</span>
        </button>
      </div>

      <div className="preset-legacy-row">
        <button
          type="button"
          role="radio"
          aria-checked={preset === null}
          className={`preset-legacy-button${preset === null ? " active" : ""}`}
          onClick={selectLegacy}
        >
          <span className="preset-legacy-kicker">Legacy</span>
          <span className="preset-legacy-title">General website (legacy)</span>
          <span className="preset-legacy-description">
            Uses the three legacy booleans (API/protocol, account/auth, commerce). Pick this
            only if you are reproducing an earlier audit.
          </span>
        </button>
      </div>

      {preset === "custom" && (
        <CustomOverridesPanel
          overrides={customOverrides}
          onChange={(next) => onChange({ preset: "custom", customOverrides: next })}
        />
      )}
    </div>
  );
}