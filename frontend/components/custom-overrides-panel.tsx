"use client";

import {
  DEFAULT_CUSTOM_OVERRIDES,
  OVERRIDE_CATEGORIES,
  OVERRIDE_LABELS,
  SECONDARY_DIMENSION_KEYS,
} from "@/constants/presets";

interface Props {
  overrides: Record<string, boolean>;
  onChange: (next: Record<string, boolean>) => void;
}

/**
 * CustomOverridesPanel
 *
 * Power-user toggle panel for the Custom preset. Groups the supported
 * override keys into four categories plus three secondary top-level
 * dimensions. Backend remains the source of truth for validation.
 */
export default function CustomOverridesPanel({ overrides, onChange }: Props) {
  function toggle(key: string, next: boolean) {
    onChange({ ...overrides, [key]: next });
  }

  function resetToDefaults() {
    onChange({ ...DEFAULT_CUSTOM_OVERRIDES });
  }

  function disableAll() {
    // Walk the default key universe — overrides may be empty on first open.
    const cleared: Record<string, boolean> = {};
    for (const key of Object.keys(DEFAULT_CUSTOM_OVERRIDES)) {
      cleared[key] = false;
    }
    onChange(cleared);
  }

  return (
    <section className="custom-overrides-panel" aria-label="Custom preset overrides">
      <header className="custom-overrides-header">
        <div>
          <p className="panel-kicker">Power user overrides</p>
          <h3>Custom / Power User toggles</h3>
          <p className="custom-overrides-subtitle">
            Defaults mirror Blog/Content.
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
            return (
              <label
                key={key}
                className={`custom-override-toggle${checked ? " active" : ""}`}
              >
                <input
                  type="checkbox"
                  checked={checked}
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
                return (
                  <label
                    key={key}
                    className={`custom-override-card${checked ? " active" : ""}`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
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