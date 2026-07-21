/**
 * Pure helpers for deriving the benchmark group filter from the applied preset.
 *
 * The named preset ids double as benchmark peer group ids after the QA5-03
 * rename, so no mapping table is needed — a preset id is a valid group id by
 * construction. This module exists so the default-derivation logic is
 * unit-testable without a component test harness.
 */

/** Preset ids that are also benchmark peer group ids. Excludes "custom". */
const NAMED_PRESETS: readonly string[] = [
  "blog",
  "corporate",
  "services",
  "ecommerce",
  "news",
  "saas",
];

/**
 * Derives the default benchmark group filter from the applied preset.
 *
 * Returns the preset id when it is a named preset AND that group exists in the
 * loaded benchmark entries. Returns "all" for null/custom presets or when the
 * preset's group is absent from the loaded peers (e.g. a forked peer file that
 * lacks the group).
 *
 * @param presetApplied - The `result.scope.preset_applied` value.
 * @param availableGroups - Group ids present in the loaded benchmark entries.
 * @returns The default group filter value.
 */
export function defaultBenchmarkGroup(
  presetApplied: string | null,
  availableGroups: readonly string[],
): string {
  if (presetApplied === null) {
    return "all";
  }
  if (!NAMED_PRESETS.includes(presetApplied)) {
    return "all";
  }
  if (!availableGroups.includes(presetApplied)) {
    return "all";
  }
  return presetApplied;
}
