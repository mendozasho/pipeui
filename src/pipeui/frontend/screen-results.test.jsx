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
import {
  ScreenResults, ResultCard, cardTypeForResult,
  fetchJson, exportTransform, validationReportPath, exportValidationReport,
  XLSX_EXPORT_MAX_ROWS,
} from "./screen-results.jsx";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
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

// ── #152: validation report is server-owned — the frontend only routes ────────
// The per-function summary rows (#253: every run listed, passes and crashes
// alike) are now built by the backend (build_results_report + file writers,
// covered by pytest). The frontend guarantee is the routing: each card kind
// downloads from its own server endpoint.
describe("validationReportPath routes by card identity (#152)", () => {
  it("a source-triggered card routes to the pipelines results-file endpoint", () => {
    expect(validationReportPath(validationCard()))
      .toBe("/pipelines/s1/export/results/file");
  });

  it("a function-triggered card routes to the validations results-file endpoint", () => {
    expect(validationReportPath({ trigger: "function", function_id: "f1" }))
      .toBe("/validations/f1/export/results/file");
  });

  it("a set-triggered card routes to the set results-file endpoint", () => {
    expect(validationReportPath({ trigger: "function", set_id: "set1", function_id: null }))
      .toBe("/pipelines/sets/set1/export/results/file");
  });

  it("a card with no routing id has no export route", () => {
    expect(validationReportPath({ trigger: "function" })).toBe(null);
    expect(validationReportPath({ trigger: "source", source_id: null })).toBe(null);
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

// ── #110: export flow — empty states, error surfacing, server-side download ────

function jsonResponse(body, ok = true, status = 200) {
  return Promise.resolve({ ok, status, json: () => Promise.resolve(body) });
}

// A file-download response mock: exportValidationReport reads the ok flag,
// Content-Disposition header, and the blob body.
function fileResponse({ filename = "sales_2026-07-17_validation.csv" } = {}) {
  return Promise.resolve({
    ok: true,
    status: 200,
    headers: {
      get: (k) => k.toLowerCase() === "content-disposition"
        ? `attachment; filename="${filename}"`
        : null,
    },
    blob: () => Promise.resolve(new Blob(["function_name\n"])),
    json: () => Promise.resolve({}),
  });
}

describe("validation export downloads the server-written report file (#152)", () => {
  function stubDownloadEnv() {
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:x"),
      revokeObjectURL: vi.fn(),
    });
    const downloads = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function () {
      downloads.push(this.getAttribute("download"));
    });
    return downloads;
  }

  it("fetches the file endpoint and saves it under the server's filename", async () => {
    const fetchSpy = vi.fn(() => fileResponse({ filename: "sales_2026-07-17_validation.csv" }));
    vi.stubGlobal("fetch", fetchSpy);
    const downloads = stubDownloadEnv();
    const flash = vi.fn();

    await exportValidationReport(validationCard(), "csv", flash);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls[0][0]).toBe("/pipelines/s1/export/results/file?format=csv");
    expect(downloads).toEqual(["sales_2026-07-17_validation.csv"]);
    expect(flash).not.toHaveBeenCalled();
  });

  it("a set-triggered card downloads from the set endpoint", async () => {
    const fetchSpy = vi.fn(() => fileResponse({ filename: "nightly_2026-07-17_validation.csv" }));
    vi.stubGlobal("fetch", fetchSpy);
    const downloads = stubDownloadEnv();

    await exportValidationReport(
      { trigger: "function", set_id: "set1", set_name: "nightly" }, "csv", vi.fn());

    expect(fetchSpy.mock.calls[0][0]).toBe("/pipelines/sets/set1/export/results/file?format=csv");
    expect(downloads).toEqual(["nightly_2026-07-17_validation.csv"]);
  });

  it("flashes the backend detail on an HTTP error — no download", async () => {
    vi.stubGlobal("fetch", vi.fn(() =>
      jsonResponse({ detail: "Function 'f1' not found" }, false, 404)));
    const downloads = stubDownloadEnv();
    const flash = vi.fn();

    await exportValidationReport(
      { trigger: "function", function_id: "f1", function_name: "chk" }, "csv", flash);

    expect(flash).toHaveBeenCalledWith(
      expect.stringMatching(/Function 'f1' not found/), "error");
    expect(downloads).toEqual([]);
  });

  it("guards a card with no routing id — flashes, zero fetches", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const flash = vi.fn();

    await exportValidationReport({ trigger: "function" }, "csv", flash);

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(flash).toHaveBeenCalledWith(
      expect.stringMatching(/no export route/), "error");
  });

  it("a validation card with no routing id renders the disabled export state", () => {
    render(React.createElement(ResultCard, {
      card: validationCard({ source_id: null }),
      selected: false,
      onToggleSelect: () => {},
    }));
    expect(screen.getByText("No data to export")).toBeTruthy();
    expect(screen.getByText("Export").closest("button").disabled).toBe(true);
  });
});

describe("fetchJson surfaces HTTP status and FastAPI detail (#110)", () => {
  it("rejects with the FastAPI detail on a non-ok response", async () => {
    vi.stubGlobal("fetch", vi.fn(() =>
      jsonResponse({ detail: "Source 'x' not found" }, false, 404)));
    await expect(fetchJson("/pipelines/x/staging/meta"))
      .rejects.toThrow("Source 'x' not found");
  });

  it("rejects with the HTTP status when the error body has no detail", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse({}, false, 500)));
    await expect(fetchJson("/x")).rejects.toThrow("Request failed (HTTP 500)");
  });

  it("rejects with a network message when fetch itself fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("boom"))));
    await expect(fetchJson("/x")).rejects.toThrow("Network error");
  });
});

describe("transform export downloads server-side (#110)", () => {
  it("navigates to the download URL after the meta preflight — never fetches rows", async () => {
    const fetchSpy = vi.fn(() => jsonResponse({ exists: true, row_count: 3, columns: ["a"] }));
    vi.stubGlobal("fetch", fetchSpy);
    const clickedHrefs = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function () {
      clickedHrefs.push(this.getAttribute("href"));
    });
    const flash = vi.fn();

    await exportTransform(transformCard(), "csv", flash);

    expect(fetchSpy).toHaveBeenCalledTimes(1); // meta only — no /staging data fetch
    expect(fetchSpy.mock.calls[0][0]).toBe("/pipelines/s1/staging/meta");
    expect(click).toHaveBeenCalledTimes(1);
    expect(clickedHrefs[0]).toBe("/pipelines/s1/export/transformed/file?format=csv");
    expect(flash).not.toHaveBeenCalled();
  });

  it("flashes 'nothing to export' when the staging table is empty — no download", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse({ exists: false, row_count: 0, columns: [] })));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const flash = vi.fn();

    await exportTransform(transformCard(), "csv", flash);

    expect(flash).toHaveBeenCalledWith(expect.stringMatching(/Nothing to export/), "error");
    expect(click).not.toHaveBeenCalled();
  });

  it("flashes the backend detail when the meta preflight fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() =>
      jsonResponse({ detail: "Source 's1' not found" }, false, 404)));
    const flash = vi.fn();

    await exportTransform(transformCard(), "csv", flash);

    expect(flash).toHaveBeenCalledWith(
      expect.stringMatching(/Source 's1' not found/), "error");
  });

  it("guards a null source_id — flashes, zero fetches (#110 issue 4)", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const flash = vi.fn();

    await exportTransform(transformCard({ source_id: null }), "csv", flash);

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(flash).toHaveBeenCalledWith(
      expect.stringMatching(/isn't tied to a single source/), "error");
  });

  it("steers xlsx over the sheet row limit to CSV — no download", async () => {
    vi.stubGlobal("fetch", vi.fn(() =>
      jsonResponse({ exists: true, row_count: XLSX_EXPORT_MAX_ROWS + 1, columns: [] })));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const flash = vi.fn();

    await exportTransform(transformCard(), "xlsx", flash);

    expect(flash).toHaveBeenCalledWith(
      expect.stringMatching(/Too many rows for Excel/), "error");
    expect(click).not.toHaveBeenCalled();
  });

  it("a null-source transform card renders the disabled export state", () => {
    render(React.createElement(ResultCard, {
      card: transformCard({ source_id: null }),
      selected: false,
      onToggleSelect: () => {},
    }));
    expect(screen.getByText("No data to export")).toBeTruthy();
    expect(screen.getByText("Export").closest("button").disabled).toBe(true);
  });
});
