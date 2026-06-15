// Component render tests for the Results screen — slice runner-execution/1.
//
// Acceptance #3: a result card renders per RunResult (counts + label).
// Acceptance #4: a mixed validation/transform set shows the correct card type per
//   result (#193 card-type), driven by RunResult function_type.
//
// Harness: vitest + jsdom (see vitest.config.js, test-setup.js). Named-export +
// __UI__ stub pattern, mirroring screen-builder.test.jsx.
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, within, fireEvent } from "@testing-library/react";
import { ScreenResults, ResultCard, cardTypeForResult, collectValidationResultRows } from "./screen-results.jsx";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function validationCard(overrides = {}) {
  return {
    run_id: "rid-v",
    card_type: "validation",
    function_type: "validation",
    trigger: "source",
    source_id: "s1",
    source_name: "sales",
    label: "amount",
    result_id: "ab12cd34",
    run_at: new Date().toISOString(),
    summary: { rows_passed: 8, rows_failed: 2, pass_rate: 0.8 },
    steps: [],
    ...overrides,
  };
}

function transformCard(overrides = {}) {
  return {
    run_id: "rid-t",
    card_type: "transform",
    function_type: "transform",
    trigger: "source",
    source_id: "s1",
    source_name: "sales",
    label: "amount",
    result_id: "ef56ab78",
    run_at: new Date().toISOString(),
    summary: { rows_affected: 100, columns: [] },
    steps: [],
    ...overrides,
  };
}

// ── Acceptance #3: a card renders per RunResult (counts + label) ───────────────
describe("Results screen renders a card per RunResult", () => {
  it("renders one card per result in the list", () => {
    render(
      React.createElement(ScreenResults, {
        flash: () => {},
        resultCards: [validationCard(), transformCard()],
        resultsContext: null,
        onNavigate: () => {},
      })
    );
    // Each card shows its label; two results -> two labels rendered.
    expect(screen.getAllByText("amount").length).toBe(2);
  });

  it("a validation card shows its pass/fail counts", () => {
    render(React.createElement(ResultCard, {
      card: validationCard(),
      selected: false,
      onToggleSelect: () => {},
    }));
    expect(screen.getByText("8")).toBeTruthy();   // rows_passed
    expect(screen.getByText("2")).toBeTruthy();   // rows_failed
  });

  it("a validation card shows its readable label", () => {
    render(React.createElement(ResultCard, {
      card: validationCard({ label: "region" }),
      selected: false,
      onToggleSelect: () => {},
    }));
    expect(screen.getByText("region")).toBeTruthy();
  });

  it("a failed run surfaces its error instead of 0/0 counts (#258)", () => {
    render(React.createElement(ResultCard, {
      card: validationCard({
        summary: { rows_passed: null, rows_failed: null, pass_rate: null },
        sources: [{
          status: "failed",
          error: "parameter 'threshold' is required but no value or default was provided",
        }],
      }),
      selected: false,
      onToggleSelect: () => {},
    }));
    expect(screen.getByText(/parameter 'threshold' is required/)).toBeTruthy();
  });
});

// ── #253: validation export lists EVERY result, passes and crashes alike ──────
// Regression: the export previously emitted only concatenated failing_rows, so a
// source whose validations mostly passed/crashed produced a CSV of "headers + one
// row". collectValidationResultRows must return one summary row per validation run.
describe("validation export lists every validation result (#253)", () => {
  // Mirrors the live `customers` run: 2 runs that executed (one with a failing
  // data row), 2 that crashed (no counts, only an error).
  const customersCard = validationCard({
    source_name: "customers",
    steps: [
      { function_name: "is_not_empty", label: "customer_id", status: "ok",
        rows_passed: 10, rows_failed: 0, failing_rows: [] },
      { function_name: "is_not_empty", label: "name", status: "ok",
        rows_passed: 9, rows_failed: 1,
        failing_rows: [{ customer_id: "c7", name: null }] },
      { function_name: "within_range", label: "customer_id", status: "failed",
        rows_passed: null, rows_failed: null, failing_rows: [],
        error: "Invalid comparison between dtype=str and float" },
      { function_name: "is_positive", label: "is_positive", status: "failed",
        rows_passed: null, rows_failed: null, failing_rows: [],
        error: "got multiple values for keyword argument 'value'" },
    ],
  });

  it("returns one row per validation run, not just failing data rows", () => {
    const rows = collectValidationResultRows(customersCard);
    expect(rows.length).toBe(4); // not 1 (the lone failing data row)
    expect(rows.map(r => r.function_name)).toEqual([
      "is_not_empty", "is_not_empty", "within_range", "is_positive",
    ]);
  });

  it("a run that executed carries ok status, counts, and a pass rate", () => {
    const rows = collectValidationResultRows(customersCard);
    const nameRow = rows.find(r => r.label === "name");
    expect(nameRow.status).toBe("ok"); // ran fine despite a failing data row
    expect(nameRow.rows_passed).toBe(9);
    expect(nameRow.rows_failed).toBe(1);
    expect(nameRow.pass_rate).toBe("90.0%");
    expect(nameRow.error).toBe(null);
  });

  it("a crashed run is still listed as a failure with its error and null counts", () => {
    const rows = collectValidationResultRows(customersCard);
    const crashed = rows.find(r => r.function_name === "is_positive");
    expect(crashed.status).toBe("failed");
    expect(crashed.rows_passed).toBe(null);
    expect(crashed.rows_failed).toBe(null);
    expect(crashed.pass_rate).toBe(null);
    expect(crashed.error).toMatch(/multiple values for keyword argument 'value'/);
  });
});

// ── Acceptance #4: correct card type per result, driven by function_type (#193) ─
describe("mixed validation/transform set shows the correct card type per result", () => {
  it("cardTypeForResult derives card type from function_type", () => {
    expect(cardTypeForResult({ function_type: "validation" })).toBe("validation");
    expect(cardTypeForResult({ function_type: "transform" })).toBe("transform");
  });

  it("a validation result renders the validation tag, a transform result the transform tag", () => {
    const { container: vContainer } = render(React.createElement(ResultCard, {
      card: validationCard(), selected: false, onToggleSelect: () => {},
    }));
    expect(within(vContainer).getByText("validation")).toBeTruthy();
    expect(within(vContainer).queryByText("transform")).toBeNull();

    const { container: tContainer } = render(React.createElement(ResultCard, {
      card: transformCard(), selected: false, onToggleSelect: () => {},
    }));
    expect(within(tContainer).getByText("transform")).toBeTruthy();
    expect(within(tContainer).queryByText("validation")).toBeNull();
  });
});

// ── Slice 5 / #244: minimal results drawer (reuses the existing Drawer) ────────
describe("opening a result card opens a minimal drawer with the RunResult metadata", () => {
  it("the drawer is closed until the card is opened", () => {
    render(React.createElement(ResultCard, {
      card: validationCard(),
      selected: false,
      onToggleSelect: () => {},
    }));
    // No drawer rendered before any open action.
    expect(screen.queryByTestId("drawer")).toBeNull();
  });

  it("opening the card renders the RunResult metadata in the reused Drawer", () => {
    render(React.createElement(ResultCard, {
      card: validationCard({ function_name: "pos_check", result_id: "ab12cd34", label: "amount" }),
      selected: false,
      onToggleSelect: () => {},
    }));
    // Open the card detail (the "Details" affordance opens the minimal drawer).
    fireEvent.click(screen.getByText("Details"));
    const drawer = screen.getByTestId("drawer");
    // RunResult metadata is rendered inside the reused Drawer component.
    expect(within(drawer).getByText("pos_check")).toBeTruthy();   // function_name
    expect(within(drawer).getByText("ab12cd34")).toBeTruthy();    // result_id (UUID5 identity)
    // The normalized label appears as both the drawer title and the Label field value.
    expect(within(drawer).getAllByText("amount").length).toBeGreaterThan(0);
  });

  it("the drawer closes via its Close control", () => {
    render(React.createElement(ResultCard, {
      card: validationCard(),
      selected: false,
      onToggleSelect: () => {},
    }));
    fireEvent.click(screen.getByText("Details"));
    expect(screen.getByTestId("drawer")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Close drawer"));
    expect(screen.queryByTestId("drawer")).toBeNull();
  });
});
