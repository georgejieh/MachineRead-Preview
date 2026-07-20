"use client";

import { useMemo } from "react";
import {
  DEFAULT_CUSTOM_OVERRIDES,
  OVERRIDE_CATEGORIES,
  OVERRIDE_LABELS,
  PRESET_DISPLAY,
  SECONDARY_DIMENSION_KEYS,
} from "@/constants/presets";
import type { Preset } from "@/lib/types";

interface Props {
  preset: Preset;
  overrides: Record<string, boolean>;
  onChange: (next: Record<string, boolean>) => void;
}

/**
 * CustomOverridesPanel
 *
 * Power-user toggle panel. Only visible when the user has selected the
 * Custom preset. Groups the supported `_VALID_OVERRIDE_KEYS` from
 * `backend/app/presets.py` into four categories (Content / Protocols /
 * Commerce / Auth) plus the three secondary top-level dimensions.
 *
 * Impossible combinations for the current base preset are disabled with a
 * tooltip explaining why. The backend remains the source of truth for
 * validation; this panel only mirrors the not-applicable set so the UI
 * stays in sync with `docs/free_preset_taxonomy.md`.
 */
export default function CustomOverridesPanel({ preset, overrides, onChange }: Props) {
  const notAvailable = useMemo(() => new Set(PRESET_DISPLAY[preset].notAvailable), [preset]);

  function toggle(key: string, next: boolean) {
    onChange({ ...overrides, [key]: next });
  }

  function resetToDefaults() {
    onChange({ ...DEFAULT_CUSTOM_OVERRIDES });
  }

  function disableAll() {
    const cleared: Record<string, boolean> = {};
    for (const key of Object.keys(overrides)) {
      cleared[key] = false;
    }
    onChange(cleared);
  }

  const presetLabel = PRESET_DISPLAY[preset].label;

  return (
    <section className="custom-overrides-panel" aria-label="Custom preset overrides">
      <header className="custom-overrides-header">
        <div>
          <p className="panel-kicker">Power user overrides</p>
          <h3>Custom / Power User toggles</h3>
          <p className="custom-overrides-subtitle">
            Defaults mirror Blog/Content. Disabled rows are not applicable for{" "}
            <strong>{presetLabel}</strong> and the backend will reject them.
          </p>
        </div>
        <div className="custom-overrides-actions">
          <button type="button" className="secondary-action" onClick={resetToDefaults}>
            Reset to defaults
          </button>
          <button type="button" className="secondary-action" onClick={disableAll}>
            Disable all
          </button>
        </div>
      </header>

      <div className="custom-overrides-secondary">
        <p className="panel-kicker">Secondary dimensions</p>
        <div className="custom-overrides-toggle-row">
          {SECONDARY_DIMENSION_KEYS.map((key) => {
            const label = OVERRIDE_LABELS[key] ?? key;
            const checked = overrides[key] === true;
            const disabled = notAvailable.has(key);
            return (
              <label
                key={key}
                className={`custom-override-toggle${checked ? " active" : ""}`}
                data-disabled={disabled ? "true" : "false"}
                title={disabled ? `Not available for ${presetLabel}` : undefined}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={disabled}
                  onChange={(event) => toggle(key, event.target.checked)}
                />
                <span>{label}</span>
              </label>
            );
          })}
        </div>
      </div>

      <div className="custom-overrides-categories">
        {OVERRIDE_CATEGORIES.map((group) => (
          <fieldset key={group.category} className="custom-overrides-group">
            <legend>{group.category}</legend>
            <div className="custom-overrides-grid">
              {group.keys.map((key) => {
                const label = OVERRIDE_LABELS[key] ?? key;
                const checked = overrides[key] === true;
                const disabled = notAvailable.has(key);
                return (
                  <label
                    key={key}
                    className={`custom-override-card${checked ? " active" : ""}`}
                    data-disabled={disabled ? "true" : "false"}
                    title={disabled ? `Not available for ${presetLabel}` : undefined}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={disabled}
                      onChange={(event) => toggle(key, event.target.checked)}
                    />
                    <span className="custom-override-name">{label}</span>
                    <span className="custom-override-key">{key}</span>
                  </label>
                );
              })}
            </div>
          </fieldset>
        ))}
      </div>
    </section>
  );
}