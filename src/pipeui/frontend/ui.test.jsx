// Component tests for the shared ui.jsx primitives added for #152:
//   - Modal  (scrim + centred dialog chrome; close on ✕ / scrim / Esc)
//   - Switch (token-native two-state toggle)
//
// Harness: vitest + jsdom (see vitest.config.js, test-setup.js). Dev-time only —
// the app itself runs no-build-step from CDN React.
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { Modal, Switch, ErrorBoundary } from "./ui.jsx";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Modal
// ---------------------------------------------------------------------------
describe("Modal", () => {
  function renderModal(extra = {}) {
    return render(
      React.createElement(
        Modal,
        {
          open: true,
          onClose: () => {},
          title: "Add join",
          footer: React.createElement("button", null, "Add step"),
          ...extra,
        },
        React.createElement("div", null, "modal body content")
      )
    );
  }

  it("renders title, body, and footer when open", () => {
    renderModal();
    expect(screen.getByText("Add join")).toBeTruthy();
    expect(screen.getByText("modal body content")).toBeTruthy();
    expect(screen.getByText("Add step")).toBeTruthy();
  });

  it("renders nothing when closed", () => {
    renderModal({ open: false });
    expect(screen.queryByText("Add join")).toBeNull();
    expect(screen.queryByText("modal body content")).toBeNull();
  });

  it("calls onClose when the close button is clicked", () => {
    const onClose = vi.fn();
    renderModal({ onClose });
    fireEvent.click(screen.getByLabelText("Close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when the scrim is clicked", () => {
    const onClose = vi.fn();
    renderModal({ onClose });
    fireEvent.click(screen.getByTestId("modal-scrim"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does NOT call onClose when the dialog body is clicked", () => {
    const onClose = vi.fn();
    renderModal({ onClose });
    fireEvent.click(screen.getByText("modal body content"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("calls onClose when Escape is pressed", () => {
    const onClose = vi.fn();
    renderModal({ onClose });
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders headerExtra content when provided", () => {
    renderModal({ headerExtra: React.createElement("span", null, "step rail here") });
    expect(screen.getByText("step rail here")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Switch
// ---------------------------------------------------------------------------
describe("Switch", () => {
  it("reflects the checked state via aria-checked", () => {
    const { rerender } = render(
      React.createElement(Switch, { checked: false, onChange: () => {} })
    );
    expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("false");
    rerender(React.createElement(Switch, { checked: true, onChange: () => {} }));
    expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("true");
  });

  it("calls onChange with the toggled value when clicked", () => {
    const onChange = vi.fn();
    render(React.createElement(Switch, { checked: false, onChange }));
    fireEvent.click(screen.getByRole("switch"));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("does not call onChange when disabled", () => {
    const onChange = vi.fn();
    render(React.createElement(Switch, { checked: false, onChange, disabled: true }));
    fireEvent.click(screen.getByRole("switch"));
    expect(onChange).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// ErrorBoundary — a render-time throw must surface a recoverable message instead
// of unmounting the tree to a blank screen.
// ---------------------------------------------------------------------------
describe("ErrorBoundary", () => {
  function Boom() {
    throw new Error("kaboom");
  }

  it("renders its children when nothing throws", () => {
    render(
      React.createElement(
        ErrorBoundary,
        null,
        React.createElement("div", null, "safe content")
      )
    );
    expect(screen.getByText("safe content")).toBeTruthy();
  });

  it("renders a fallback (not a blank screen) when a child throws", () => {
    // React logs the caught error to console.error; silence the expected noise.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(React.createElement(ErrorBoundary, null, React.createElement(Boom)));
    expect(screen.getByText("Something went wrong")).toBeTruthy();
    expect(screen.getByText(/kaboom/)).toBeTruthy();
    spy.mockRestore();
  });
});
