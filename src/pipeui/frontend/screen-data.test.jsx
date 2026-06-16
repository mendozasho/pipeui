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
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { MigrationConfirmModal } from "./screen-data.jsx";

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
