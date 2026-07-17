// Component tests for the Data screen's column-type migration modal.
//
// Guards the black-screen regression: MigrationConfirmModal previously rendered
// each shared-source entry (an object {source_id, source_name}) directly as a
// React child, throwing "Objects are not valid as a React child" and — with no
// error boundary — blanking the whole app.
//
// Harness: vitest + jsdom (see vitest.config.js, test-setup.js). Dev-time only —
// the app itself runs no-build-step from CDN React.
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { MigrationConfirmModal, RegisterModal, patternGroupLabel } from "./screen-data.jsx";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const SHARED = [
  { source_id: "id-1", source_name: "claims_2024" },
  { source_id: "id-2", source_name: "premiums_q1" },
];

describe("MigrationConfirmModal", () => {
  it("renders shared-source NAMES (not the objects) without crashing", () => {
    render(
      React.createElement(MigrationConfirmModal, {
        uncastable: 3,
        sharedSources: SHARED,
        onConfirm: () => {},
        onCancel: () => {},
      })
    );
    // The source_name string renders; the object never reaches the DOM.
    expect(screen.getByText("claims_2024")).toBeTruthy();
    expect(screen.getByText("premiums_q1")).toBeTruthy();
  });

  it("renders the uncastable-count message", () => {
    render(
      React.createElement(MigrationConfirmModal, {
        uncastable: 3,
        sharedSources: [],
        onConfirm: () => {},
        onCancel: () => {},
      })
    );
    expect(screen.getByText(/will become NULL/)).toBeTruthy();
  });

  it("passes the selected scope to onConfirm", () => {
    const onConfirm = vi.fn();
    render(
      React.createElement(MigrationConfirmModal, {
        uncastable: 0,
        sharedSources: SHARED,
        onConfirm,
        onCancel: () => {},
      })
    );
    fireEvent.click(screen.getByText("Migrate anyway"));
    // Default scope is "this_source".
    expect(onConfirm).toHaveBeenCalledWith("this_source");
  });
});

// ---------------------------------------------------------------------------
// patternGroupLabel (#156) — the stored source `pattern` is a regex from
// infer_pattern with digit runs generalized to literal `\d+` tokens; the group
// header must show the filename convention (prefix up to the first number + *),
// never the raw regex.
// ---------------------------------------------------------------------------
describe("patternGroupLabel", () => {
  it("turns a single-token pattern into the prefix plus a wildcard", () => {
    expect(patternGroupLabel("sales_\\d+")).toBe("sales_*");
  });

  it("collapses a multi-part date pattern to one wildcard — never the raw regex", () => {
    expect(patternGroupLabel("sales-\\d+.\\d+.\\d+")).toBe("sales-*");
  });

  it("returns null for a missing pattern (source stays ungrouped)", () => {
    expect(patternGroupLabel(null)).toBe(null);
    expect(patternGroupLabel(undefined)).toBe(null);
    expect(patternGroupLabel("")).toBe(null);
  });

  it("passes through a pattern with no digit token unchanged", () => {
    expect(patternGroupLabel("static_name")).toBe("static_name");
  });
});

// ---------------------------------------------------------------------------
// RegisterModal — column headers come from the backend peek-columns endpoint,
// not from client-side workbook parsing.
// ---------------------------------------------------------------------------
describe("RegisterModal", () => {
  it("populates the primary-key dropdown from /sources/peek-columns", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({ columns: ["id", "name", "value"] }) })
    );
    render(
      React.createElement(RegisterModal, {
        file: { name: "data.xlsx" },
        onConfirm: () => {},
        onCancel: () => {},
      })
    );
    await waitFor(() => expect(screen.getByText("value")).toBeTruthy());
    expect(global.fetch).toHaveBeenCalledWith(
      "/sources/peek-columns",
      expect.objectContaining({ method: "POST" })
    );
    // First column becomes the default primary key (PK select is the first combobox).
    expect(screen.getAllByRole("combobox")[0].value).toBe("id");
  });

  it("falls back to a text input and logs (not silent) when peek-columns fails", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    global.fetch = vi.fn(() => Promise.resolve({ ok: false, status: 500 }));
    render(
      React.createElement(RegisterModal, {
        file: { name: "data.xlsx" },
        onConfirm: () => {},
        onCancel: () => {},
      })
    );
    await waitFor(() => expect(console.error).toHaveBeenCalled());
    // No detected columns → the primary-key field is a plain text input.
    expect(screen.getByPlaceholderText("e.g. id")).toBeTruthy();
    spy.mockRestore();
  });
});
