import assert from "node:assert/strict";
import test from "node:test";
import { defaultBenchmarkGroup } from "./benchmark-defaults.ts";

test("defaults a named preset to its group when available", () => {
  assert.equal(defaultBenchmarkGroup("blog", ["blog", "news"]), "blog");
});

test("defaults a custom preset to all groups", () => {
  assert.equal(defaultBenchmarkGroup("custom", ["blog", "news"]), "all");
});

test("defaults a missing preset to all groups", () => {
  assert.equal(defaultBenchmarkGroup(null, ["blog", "news"]), "all");
});

test("falls back to all groups when the named preset is unavailable", () => {
  assert.equal(defaultBenchmarkGroup("saas", ["blog", "news"]), "all");
});

test("falls back to all groups when available groups is empty", () => {
  assert.equal(defaultBenchmarkGroup("blog", []), "all");
});
