// Unit tests for the result-card builders (#110).
//
// An empty run (sources: []) must build NO card — the screen flashes an
// explanation instead of committing a 0/0 "success" card to the Results grid.
//
// Harness: vitest + jsdom (see vitest.config.js, test-setup.js). Named-export
// pattern, mirroring screen-results.test.jsx.
import { describe, it, expect } from "vitest";
import { buildSetRunCard, buildFunctionRunCard } from "./screen-modules.jsx";

describe("buildFunctionRunCard (#110)", () => {
  it("returns null when the run touched no sources", () => {
    expect(buildFunctionRunCard({ function_id: "f1", function_name: "fn", sources: [] })).toBe(null);
    expect(buildFunctionRunCard({ function_id: "f1", function_name: "fn" })).toBe(null);
    expect(buildFunctionRunCard(null)).toBe(null);
  });

  it("builds a function-triggered card with aggregated counts", () => {
    const card = buildFunctionRunCard({
      function_id: "f1",
      function_name: "is_positive",
      sources: [
        { source_name: "sales", rows_passed: 8, rows_failed: 2 },
        { source_name: "customers", rows_passed: 5, rows_failed: 5 },
      ],
    });
    expect(card.trigger).toBe("function");
    expect(card.source_id).toBe(null);
    expect(card.function_name).toBe("is_positive");
    expect(card.summary.rows_passed).toBe(13);
    expect(card.summary.rows_failed).toBe(7);
    expect(card.summary.pass_rate).toBeCloseTo(0.65);
    expect(card.sources.length).toBe(2);
  });

  it("a crashed run (null counts) keeps a null pass rate, not 0/0", () => {
    const card = buildFunctionRunCard({
      function_id: "f1",
      function_name: "fn",
      sources: [{ source_name: "sales", status: "failed", error: "boom" }],
    });
    expect(card).not.toBe(null);
    expect(card.summary.pass_rate).toBe(null);
  });
});

describe("buildSetRunCard (#110)", () => {
  it("returns null when the run touched no sources", () => {
    expect(buildSetRunCard({ set_id: "s1", set_name: "checks", sources: [] })).toBe(null);
    expect(buildSetRunCard({ set_id: "s1", set_name: "checks" })).toBe(null);
    expect(buildSetRunCard(null)).toBe(null);
  });

  it("builds a set-triggered card aggregating across sources and steps", () => {
    const card = buildSetRunCard({
      set_id: "s1",
      set_name: "checks",
      sources: [
        { steps: [{ rows_passed: 4, rows_failed: 1 }, { rows_passed: 5, rows_failed: 0 }] },
        { steps: [{ rows_passed: 3, rows_failed: 2 }] },
      ],
    });
    expect(card.trigger).toBe("function");
    expect(card.source_id).toBe(null);
    expect(card.set_name).toBe("checks");
    expect(card.summary.rows_passed).toBe(12);
    expect(card.summary.rows_failed).toBe(3);
    expect(card.summary.pass_rate).toBeCloseTo(0.8);
  });
});
