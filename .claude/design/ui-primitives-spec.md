# PipeUI — Shared UI Primitives (Session 1)

Spec **and** migration plan for three new primitives plus one error-surfacing
rule. Components are added to `frontend/ui.jsx` and exported via
`window.__UI__`. This file is the implementation reference and the rollout
checklist — work top to bottom.

## Constraints (apply to every component)

- Inline styles only — no CSS classes, no stylesheet additions.
- No new external dependencies.
- `Spinner` animates via SVG `<animateTransform>`, so it needs **no
  `@keyframes`** and honours the inline-styles-only rule.
- Reuse existing tokens; introduce none.

Token reference (defined in `index.html`):

```
bg      --bg #0e0e10 · --panel #141416 · --panel-2 #1a1a1d · --panel-3 #202024
border  --border rgba(255,255,255,.08) · --border-soft rgba(255,255,255,.04) · --hover rgba(255,255,255,.06)
text    --text #f0f0f2 · --text-2 #a0a0aa · --text-3 #6a6a74 · --text-4 #3a3a42
semantic --good #34d399 · --bad #f87171 · --warn #fbbf24 · --run #60a5fa
accent  --accent #7c6cf5 · --accent-soft · --accent-line · --accent-ink #fff
radius  --radius 6px · --radius-lg 10px      font  Geist / Geist Mono
```

---

# Part 1 — New primitives

## 1a · `Spinner`

A compact "working" signal for inside buttons, beside labels, or anywhere a
tight progress cue is needed. Colour comes from context: the default
`currentColor` means dropping it inside a `Btn` makes it adopt the button's ink
automatically.

| Prop | Type | Default | Notes |
|---|---|---|---|
| `size` | number | `14` | Px width/height. 14 fits a md `Btn`; bump to 20–32 standalone. |
| `color` | string | `"currentColor"` | Any colour or CSS var. Inherits surrounding text colour by default. |
| `strokeWidth` | number | `2` | Ring thickness. Scale up (2.5–3) at larger sizes. |

**Behaviour.** A faint full ring at `strokeOpacity 0.22` sits under a ~30%-length
rotating arc, 0.7s linear loop. Fixed-size inline SVG — no layout shift. Pass a
token when standalone (`var(--text-3)` neutral, `var(--accent)` / `var(--run)`
for emphasis).

```jsx
// Pure SVG — no @keyframes, no stylesheet, no external lib.
function Spinner({ size = 14, color = "currentColor", strokeWidth = 2 }) {
  const c = size / 2;
  const r = (size - strokeWidth) / 2;
  const circ = 2 * Math.PI * r;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}
         role="status" aria-label="Loading"
         style={{ flexShrink: 0, display: "block" }}>
      <circle cx={c} cy={c} r={r} fill="none" stroke={color}
              strokeOpacity={0.22} strokeWidth={strokeWidth} />
      <circle cx={c} cy={c} r={r} fill="none" stroke={color}
              strokeWidth={strokeWidth} strokeLinecap="round"
              strokeDasharray={`${circ * 0.3} ${circ}`}>
        <animateTransform attributeName="transform" type="rotate"
          from={`0 ${c} ${c}`} to={`360 ${c} ${c}`}
          dur="0.7s" repeatCount="indefinite" />
      </circle>
    </svg>
  );
}

// Usage — inherits the button's ink:
<Btn variant="primary" disabled={saving}>
  {saving ? <><Spinner size={13} color="var(--accent-ink)" /> Ingesting…</> : "Run ingestion"}
</Btn>
```

## 1b · `LoadingState`

The full-panel placeholder that replaces every ad-hoc `"Loading…"` div. Centred
and muted so a fetching list and the `DataTable` "No data yet" empty-state read
as the same family.

| Prop | Type | Default | Notes |
|---|---|---|---|
| `label` | string | `"Loading…"` | Override per context — `"Loading sources…"`, `"Loading functions…"`. |
| `size` | number | `22` | Spinner px. The standalone default; rarely changed. |

**Tokens.** Spinner + label both at `--text-3`; 40px vertical padding; 12px
label, `.02em` tracking — matches the empty-state weight.

```jsx
function LoadingState({ label = "Loading…", size = 22 }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center",
                  justifyContent: "center", gap: 10, padding: "40px 20px",
                  color: "var(--text-3)" }}>
      <Spinner size={size} color="var(--text-3)" strokeWidth={2.5} />
      <span style={{ fontSize: 12, letterSpacing: ".02em" }}>{label}</span>
    </div>
  );
}
```

## 1c · `InlineError`

One component, two placements. `variant="field"` is bare icon + text that tucks
under an input; `variant="panel"` is a tinted block for drawer sections and
failed actions. Auto-clears when `children` is falsy.

| Prop | Type | Default | Notes |
|---|---|---|---|
| `children` | node | — | The message. Falsy → renders nothing, so it auto-clears on a valid input. |
| `variant` | `"field" \| "panel"` | `"field"` | field: bare, under inputs. panel: tinted block, in drawers/actions. |
| `onDismiss` | function | `undefined` | Shows a close affordance. Omit for validation that should clear itself. |
| `style` | object | `undefined` | Escape hatch for spacing in unusual layouts. |

**Tokens.** Text & icon at `--bad`, reusing the existing `warn` glyph from
`Icon`. Panel variant adds a 10%/35% `--bad` tint + border at `--radius` —
deliberately quieter than the `Flash` toast since it doesn't need to grab
attention from across the screen.

```jsx
function InlineError({ children, variant = "field", onDismiss, style }) {
  if (!children) return null;                    // falsy message → auto-clear
  const isPanel = variant === "panel";
  return (
    <div role="alert" style={{
      display: "flex", alignItems: "flex-start", gap: 7,
      color: "var(--bad)", fontSize: 12, lineHeight: 1.45,
      ...(isPanel
        ? { padding: "8px 11px", background: "rgba(248,113,113,.1)",
            border: "1px solid rgba(248,113,113,.35)", borderRadius: "var(--radius)" }
        : { padding: "5px 0 0" }),
      ...style,
    }}>
      <Icon name="warn" size={13} style={{ marginTop: 1 }} />
      <span style={{ flex: 1 }}>{children}</span>
      {onDismiss && (
        <button onClick={onDismiss} aria-label="Dismiss"
          style={{ background: "none", border: "none", cursor: "pointer",
                   color: "inherit", padding: 0, marginTop: 1, opacity: .65 }}>
          <Icon name="close" size={11} />
        </button>
      )}
    </div>
  );
}
```

---

# Part 2 — Error surfacing decision rule

Today errors are split three ways — `Flash` toast, `console.error`-only
(invisible), and form errors crammed into the toast. One rule, applied
everywhere:

- **`Flash` (toast)** — transient, system-level outcomes **not** bound to a
  visible control: network failures, request-level server errors, and success
  confirmations. Fires bottom-right, auto-dismisses. Use when the cause may
  scroll out of view. _e.g._ `flash("Network error during ingestion.", "error")`.
- **`InlineError`** — errors tied to a specific field, form, or panel the user
  is working in: validation, and failed actions whose context is still on
  screen. Persists at the cause until corrected or dismissed. _e.g._ the column
  migration error in the source drawer; "Set name is required" under the input.
- **`console.error` — never alone.** Acceptable only as an _additional_
  developer log. Every failure that blocks or changes what the user sees must
  also surface via `Flash` or `InlineError`. The current
  console-error-only paths in `screen-data.jsx` are bugs (see m… items below).

---

# Part 3 — Export registration

```jsx
// end of ui.jsx
window.__UI__ = {
  Icon, Btn, KindTag, StatusPill, SourceBadge, DataTable, Flash, Drawer,
  Spinner, LoadingState, InlineError,   // ← added this session
};
```

Then add `Spinner` / `LoadingState` / `InlineError` to each screen's
`const { … } = window.__UI__;` destructure as noted per file below.

---

# Part 4 — Migration checklist

21 call sites across the five screens. Swap types: **L** = `LoadingState`,
**S** = `Spinner` in a button, **E** = `InlineError`. Line numbers are against
`main`. Items marked **⚠ bug** also fix a defect.

### Defects fixed along the way

- **⚠ Silent failure** — `screen-data.jsx` L303–306, L325–328: column-type
  migration `catch` blocks log to console only; user sees nothing.
- **⚠ Renders as success** — `screen-settings.jsx` L207, L210:
  `flash(…, "err")` — Flash only matches `"error"`, so a failed save shows green.
- **⚠ Off-palette** — `screen-builder.jsx` L876–878: pipeline error uses
  hardcoded `#e05252` instead of `--bad`.
- **⚠ Wrong pattern** — `screen-modules.jsx` L244: "Set name is required" uses a
  floating toast for field validation.

---

## `screen-data.jsx`

**Prereq:** add `Spinner, LoadingState, InlineError` to the `window.__UI__`
destructure (alongside `DataTable, SourceBadge, StatusPill, Icon`).

- [ ] **d1 · ⚠ bug (E) — column-type migration swallows errors** · L303–306, L325–328
  **New state:** `const [migrationError, setMigrationError] = useState(null);`
  Both `catch` blocks are silent; surface the failure in the column drawer and
  keep the log.
  ```diff
  - } catch (err) {
  -   console.error("Column type migration error:", err);
  -   setSelected(prev);
  - }
  + } catch (err) {
  +   console.error("Column type migration error:", err);   // keep dev log
  +   setSelected(prev);
  +   setMigrationError("Couldn’t apply the type change — the source is unchanged.");
  + }
  +
  + // in the column drawer body:
  + <InlineError variant="panel" onDismiss={() => setMigrationError(null)}>
  +   {migrationError}
  + </InlineError>
  ```

- [ ] **d2 (L) — source preview placeholder** · L541
  ```diff
  - <div style={{ color: "var(--text-3)", fontSize: 12, padding: "8px 0" }}>Loading…</div>
  + <LoadingState label="Loading preview…" />
  ```

- [ ] **d3 (L) — sources table has no loading state** · L620–628
  `DataTable` renders its "No data yet" empty row even while fetching. Gate it.
  ```diff
  - <DataTable columns={columns} rows={sources} … />
  + {loading
  +   ? <LoadingState label="Loading sources…" />
  +   : <DataTable columns={columns} rows={sources} … />}
  ```

- [ ] **d4 (S) — ingest-file button** · L469–472
  ```diff
  - <Btn variant="ghost" … disabled={ingesting}>
  -   {ingesting ? "Ingesting…" : "Ingest file"}
  - </Btn>
  + <Btn variant="ghost" … disabled={ingesting}>
  +   {ingesting ? <><Spinner size={13} /> Ingesting…</> : "Ingest file"}
  + </Btn>
  ```

- [ ] **d5 (S) — per-row Run button (raw `<button>`)** · L771–783
  It's a bare `<button>` with no flex — add `display:inline-flex` + `gap` so the
  spinner sits beside the label.
  ```diff
  - <button … disabled={isRunning} style={{ … whiteSpace: "nowrap" }}>
  -   {isRunning ? "Running…" : "Run"}
  - </button>
  + <button … disabled={isRunning} style={{ … display: "inline-flex",
  +   alignItems: "center", gap: 6, whiteSpace: "nowrap" }}>
  +   {isRunning ? <><Spinner size={12} color="var(--text-3)" /> Running…</> : "Run"}
  + </button>
  ```

## `screen-modules.jsx`

**Prereq:** add `Spinner, LoadingState, InlineError` to the `window.__UI__` destructure.

- [ ] **m1 · ⚠ bug (E) — "Set name is required" fires a toast** · L244
  **New state:** `const [nameError, setNameError] = useState(null);` (clear it in
  the name input's `onChange`). Field validation belongs at the field.
  ```diff
  - if (!setName.trim()) { flash && flash("Set name is required.", "error"); return; }
  + if (!setName.trim()) { setNameError("Set name is required."); return; }
  +
  + // under the name <input>:
  + <InlineError>{nameError}</InlineError>
  ```

- [ ] **m2 (L) — functions list placeholder** · L783–785
  ```diff
  - {loading && (
  -   <div style={{ color: "var(--text-3)", fontSize: 13 }}>Loading functions…</div>
  - )}
  + {loading && <LoadingState label="Loading functions…" />}
  ```

- [ ] **m3 (L) — sets list shows nothing while loading** · L509–510
  Only the empty + loaded states render today; add the loading branch.
  ```diff
  + {setsLoading && <LoadingState label="Loading sets…" />}
    {!setsLoading && sets.length === 0 && ( … )}
  ```

- [ ] **m4 (S) — run buttons (functions & sets tabs)** · L612–614, L283–285
  Both handlers track `runningFns[id]` / `runningSets[id]`. Swap the text for
  Spinner + label in each run button.
  ```diff
  - {running ? "Running…" : "Run"}
  + {running ? <><Spinner size={12} /> Running…</> : "Run"}
  ```

- [ ] **m5 (S) — header counts (optional, low priority)** · L503, L709
  The "Loading…" count text is fine as-is; add a `<Spinner size={12} />` beside
  it only if you want motion.
  ```diff
  - {loading ? "Loading…" : `${functions.length} functions registered`}
  + {loading ? <><Spinner size={12} /> Loading…</> : `${functions.length} functions registered`}
  ```

## `screen-builder.jsx`

**Prereq:** add `Spinner, LoadingState, InlineError` to the `window.__UI__` destructure.

- [ ] **b1 · ⚠ bug (E) — pipeline-load error uses a hardcoded hex** · L876–878
  `#e05252` isn't a token — fixes the colour and the pattern at once.
  ```diff
  - {error && (
  -   <div style={{ color: "#e05252", fontSize: 13 }}>{error}</div>
  - )}
  + {error && <InlineError variant="panel">{error}</InlineError>}
  ```

- [ ] **b2 (L) — functions/sets palette list** · L682
  ```diff
  - {loading && <div style={{ fontSize: 11, color: "var(--text-4)", textAlign: "center", paddingTop: 20 }}>Loading...</div>}
  + {loading && <LoadingState label="Loading…" />}
  ```

- [ ] **b3 (L) — pipeline canvas placeholder** · L873–875
  ```diff
  - {loading && (
  -   <div style={{ color: "var(--text-4)", fontSize: 13, textAlign: "center", paddingTop: 30 }}>Loading...</div>
  - )}
  + {loading && <LoadingState />}
  ```

- [ ] **b4 (L) — dry-run parameter fetch** · L870–872
  ```diff
  - {pendingDryRunning && (
  -   <div style={{ … }}>Loading parameters...</div>
  - )}
  + {pendingDryRunning && <LoadingState label="Loading parameters…" />}
  ```

## `screen-results.jsx`

**Prereq:** add `LoadingState, InlineError` to the `window.__UI__` destructure.

- [ ] **r1 (L) — transform staging fetch** · L315–317
  ```diff
  - if (loading) {
  -   return <div style={{ color: "var(--text-4)", fontSize: 13 }}>Loading staging data…</div>;
  - }
  + if (loading) return <LoadingState label="Loading staging data…" />;
  ```

- [ ] **r2 (E) — staging-load error** · L318–320
  Already on `--bad` — swap to the component for consistency.
  ```diff
  - if (error) {
  -   return <div style={{ color: "var(--bad)", fontSize: 13 }}>{error}</div>;
  - }
  + if (error) return <InlineError variant="panel">{error}</InlineError>;
  ```

## `screen-settings.jsx`

**Prereq:** add `Spinner, LoadingState, InlineError` to the `window.__UI__` destructure.

- [ ] **s1 · ⚠ bug (E) — `flash` kind `"err"` is not `"error"`** · L207, L210
  `Flash` only special-cases `kind === "error"`. `"err"` falls through to the
  green success style — a failed save looks like it worked. Fix both sites.
  ```diff
  - flash("Failed to save settings", "err");
  + flash("Failed to save settings", "error");
  ```

- [ ] **s2 (L) — directory browser placeholder** · L92–94
  ```diff
  - {loading && (
  -   <div style={{ padding: "20px 16px", color: "var(--text-3)", fontSize: 13 }}>Loading…</div>
  - )}
  + {loading && <LoadingState label="Loading…" />}
  ```

- [ ] **s3 (E) — directory browse error** · L95–97
  Also fixes the non-existent `var(--danger, #e55)` token.
  ```diff
  - {error && (
  -   <div style={{ padding: "20px 16px", color: "var(--danger, #e55)", fontSize: 13 }}>{error}</div>
  - )}
  + {error && <InlineError variant="panel">{error}</InlineError>}
  ```

- [ ] **s4 (L) — initial config load (full screen)** · L216–220
  ```diff
  - if (!loaded) return (
  -   <div style={{ flex: 1, display: "flex", … }}>Loading…</div>
  - );
  + if (!loaded) return <LoadingState label="Loading settings…" />;
  ```

- [ ] **s5 (S) — save button** · L358–360
  ```diff
  - <Btn variant="primary" onClick={handleSave} disabled={saving}>
  -   {saving ? "Saving…" : "Save"}
  - </Btn>
  + <Btn variant="primary" onClick={handleSave} disabled={saving}>
  +   {saving ? <><Spinner size={13} color="var(--accent-ink)" /> Saving…</> : "Save"}
  + </Btn>
  ```

---

## Out of scope (follow-up)

The Flash `kind` strings are inconsistent across the codebase — `"ok"`,
`"success"`, `"err"`, `"error"`. `Flash` only special-cases `"error"`, so
everything else renders as success. Item **s1** fixes the one that renders
wrong; a separate change should standardise the signature to `"ok" | "error"`
and update all call sites.
