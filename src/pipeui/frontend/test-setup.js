// Test harness setup for the no-build-step frontend.
//
// The browser app loads React/ReactDOM from CDN as globals and exposes shared
// primitives on window.__UI__. The screen components read `React` at module-eval
// (`const { useState } = React`) and `window.__UI__` at render. To import them
// under vitest/jsdom we must reproduce those globals BEFORE the component module
// is evaluated — hence this setupFile, which runs before any test module imports.
import React from "react";

globalThis.React = React;
if (typeof window !== "undefined") {
  window.React = React;
}

// Minimal stub of the shared UI primitives the builder screen consumes.
// Checkbox is the only one the parameter-mapping modal uses; it is rendered as a
// presentational element. We intentionally do NOT attach an onChange handler that
// re-toggles — the row owns the toggle (see #189) — but we honour an onChange if
// the component wires one so existing behaviour is observable.
function Checkbox({ checked, onChange, disabled }) {
  return React.createElement("input", {
    type: "checkbox",
    checked: !!checked,
    disabled: !!disabled,
    onChange: onChange || (() => {}),
    readOnly: !onChange,
    "data-testid": "checkbox",
  });
}

// Modal stub — renders title / caption / headerExtra / children / footer and a
// Close button wired to onClose. Faithful enough for the builder's JoinModal tests
// without re-importing the real ui.jsx (a static import would hoist above the
// React global set above and eval ui.jsx with React undefined).
function Modal({ open, onClose, title, caption, footer, children, headerExtra }) {
  if (!open) return null;
  return React.createElement(
    "div",
    { "data-testid": "modal" },
    React.createElement("div", null, title),
    caption ? React.createElement("div", null, caption) : null,
    headerExtra || null,
    React.createElement("button", { "aria-label": "Close", onClick: onClose }, "x"),
    children,
    footer || null
  );
}

function Switch({ checked, onChange, disabled }) {
  return React.createElement("span", {
    role: "switch",
    "aria-checked": !!checked,
    "data-testid": "switch",
    onClick: () => !disabled && onChange && onChange(!checked),
  });
}

// Btn stub — renders its children (the label) inside a real <button> so getByText
// resolves to the button and `.disabled` reflects the disabled prop.
function Btn({ children, onClick, disabled, type }) {
  return React.createElement(
    "button",
    { onClick, disabled: !!disabled, type: type || "button" },
    children
  );
}

const stubUI = {
  Checkbox,
  Modal,
  Switch,
  Btn,
  Icon: (props) => React.createElement("span", { "data-icon": props.name }),
  Spinner: () => React.createElement("span", { "data-testid": "spinner" }),
  // Other primitives the builder may touch in non-modal paths — harmless stubs.
  OrderBadge: (props) => React.createElement("span", null, props.n),
  // Drawer stub — faithful to ui.jsx Drawer: renders title + children only when
  // `open`, and wires a Close button to onClose. The minimal results drawer
  // (slice 5 / #244) reuses this component, so the stub must gate on `open`.
  Drawer: (props) =>
    props.open
      ? React.createElement(
          "div",
          { "data-testid": "drawer" },
          React.createElement("div", null, props.title),
          React.createElement(
            "button",
            { "aria-label": "Close drawer", onClick: props.onClose },
            "x"
          ),
          props.children
        )
      : null,
  KindTag: (props) => React.createElement("span", null, props.kind),
  StatusPill: (props) => React.createElement("span", null, props.status),
  // Mirrors the real SourceBadge (initials), so the full source name is not
  // duplicated in the DOM — keeps getByText(sourceName) unambiguous.
  SourceBadge: (props) => React.createElement("span", null, (props.name || "").slice(0, 2).toUpperCase()),
  LoadingState: () => React.createElement("div", null, "loading"),
  InlineError: (props) => React.createElement("div", null, props.children),
};

if (typeof window !== "undefined") {
  window.__UI__ = stubUI;
}
globalThis.__UI__ = stubUI;
