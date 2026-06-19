// Report Builder screen — Phase E2: pipeline canvas with drag-to-reorder + run controls
const { useState, useEffect, useRef, useMemo } = React;

// ---------------------------------------------------------------------------
// Result tag helpers
// ---------------------------------------------------------------------------

const RESULT_TAG_STYLES = {
  success: { bg: "#d1fae5", color: "#065f46" },
  issues:  { bg: "#fef3c7", color: "#92400e" },
  error:   { bg: "#fee2e2", color: "#991b1b" },
};

function deriveResultTag(stepResult) {
  if (!stepResult) return null;
  if (stepResult.status === "failed") return "error";
  if (stepResult.status === "ok" && stepResult.function_type === "validation" && stepResult.rows_failed > 0) return "issues";
  return "success";
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function deriveSetType(functions) {
  if (!functions || functions.length === 0) return "Unknown";
  const types = new Set(functions.map(fn => fn.function_type));
  if (types.size === 1) {
    const t = [...types][0];
    if (t === "validation") return "Validation";
    if (t === "transform") return "Transform";
  }
  return "Mixed";
}

const TYPE_BADGE_COLORS = {
  Validation: { bg: "#e8f4fd", color: "#2980b9" },
  Transform:  { bg: "#eafaf1", color: "#27ae60" },
  Mixed:      { bg: "#fef9e7", color: "#d68910" },
  Unknown:    { bg: "var(--panel-3)", color: "var(--text-3)" },
};

// ---------------------------------------------------------------------------
// Param display
// ---------------------------------------------------------------------------

function ParamRow({ param }) {
  const isDataFrame = param.param_type === "pd.DataFrame";
  return (
    <div style={{
      padding: "5px 0",
      borderBottom: "1px solid var(--border-soft)",
      display: "flex", flexDirection: "column", gap: 2,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ fontFamily: "'Geist Mono', monospace", fontSize: 11, color: "var(--text)" }}>
          {param.param_name}
        </span>
        <span style={{
          fontSize: 10, padding: "1px 5px", borderRadius: 99,
          background: "var(--panel-3)", color: "var(--text-3)",
          fontFamily: "'Geist Mono', monospace",
        }}>
          {param.param_type}
        </span>
      </div>
      {isDataFrame ? (
        <span style={{ fontSize: 10, color: "var(--text-4)", fontStyle: "italic" }}>auto (full table)</span>
      ) : param.bindings && param.bindings.length > 0 ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 3 }}>
          {param.bindings.map(b => (
            <span key={b.column_id} style={{
              fontSize: 10, padding: "1px 6px", borderRadius: 99,
              background: "var(--accent-soft)", color: "var(--accent)",
              fontFamily: "'Geist Mono', monospace",
            }}>
              {b.column_name}
            </span>
          ))}
        </div>
      ) : param.scalar_value != null && param.scalar_value !== "" ? (
        <span style={{
          fontSize: 10, padding: "1px 6px", borderRadius: 99,
          background: "var(--accent-soft)", color: "var(--accent)",
          fontFamily: "'Geist Mono', monospace", alignSelf: "flex-start",
        }}>
          = {param.scalar_value}
        </span>
      ) : (
        <span style={{ fontSize: 10, color: "var(--text-4)", fontStyle: "italic" }}>unbound</span>
      )}
    </div>
  );
}

function FunctionRow({ fn }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ borderBottom: "1px solid var(--border-soft)" }}>
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          display: "flex", alignItems: "center", gap: 7,
          padding: "5px 4px", cursor: "pointer", userSelect: "none",
        }}
      >
        <span style={{ fontSize: 10, color: "var(--text-4)", width: 10, flexShrink: 0 }}>
          {open ? "▾" : "▸"}
        </span>
        <span style={{ fontSize: 12, fontWeight: 500 }}>{fn.function_name}</span>
        <span style={{
          fontSize: 10, padding: "1px 5px", borderRadius: 99,
          background: "var(--panel-3)", color: "var(--text-3)",
          fontFamily: "'Geist Mono', monospace",
        }}>
          {fn.function_type}
        </span>
      </div>
      {open && fn.params && fn.params.length > 0 && (
        <div style={{ paddingLeft: 16, paddingBottom: 4 }}>
          {fn.params.map(p => <ParamRow key={p.param_id} param={p} />)}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pending step card — shown after dry-run, before commit
// ---------------------------------------------------------------------------

function PendingStepCard({ dryRunResult, stepName, onSave, onCancel, saving, saveError }) {
  const { Checkbox } = window.__UI__;
  const params = dryRunResult.params || [];
  // available_columns is computed server-side from source columns + join step columns
  const availableColumns = dryRunResult.available_columns || [];

  // str params toggle between "text" (scalar) and "column" (column-backed) mode.
  // On edit re-open, derive the mode from persisted state: existing column
  // bindings → "column"; otherwise "text". (#191/#192)
  const [strModes, setStrModes] = React.useState(() => {
    const m = {};
    params.forEach(p => {
      if (p.param_type === "str") {
        m[p.param_id] = (p.current_bindings || []).length > 0 ? "column" : "text";
      }
    });
    return m;
  });

  function initSelections() {
    const sel = {};
    params.forEach(p => {
      const restored = (p.current_bindings || []).map(b => b.column_id);
      if (restored.length > 0) {
        // Edit re-open: restore the step's saved column bindings (#191). Applies
        // to pd.Series/column_backed params and to a str in column-backed mode.
        sel[p.param_id] = restored;
      } else if (p.param_type === "pd.Series" || p.param_type === "column_backed") {
        // Initial attach: fall back to cross-source suggested columns.
        sel[p.param_id] = (p.suggested_columns || []).map(c => c.column_id);
      }
    });
    return sel;
  }
  const [selections, setSelections] = React.useState(initSelections);

  // Pre-populate scalar values from current_scalar_value — for scalar params AND
  // a str in plain-string mode — so a re-opened step restores its typed value. (#191)
  function initScalarValues() {
    const sv = {};
    params.forEach(p => {
      if (p.current_scalar_value != null) sv[p.param_id] = p.current_scalar_value;
    });
    return sv;
  }
  const [scalarValues, setScalarValues] = React.useState(initScalarValues);

  // Slice 4 (#241): output_mode + replace-target / append-name selection live in
  // the mapping modal. Default append; an optional append name names the new column.
  // Replace shows one ordered target picker per bound column (bundle i -> target i),
  // defaulting to the input column. Both flow to onSave's third `extras` arg.
  const [outputMode, setOutputMode] = React.useState(dryRunResult.output_mode || "append");
  const [appendName, setAppendName] = React.useState("");
  // replaceTargets: param_id -> [column_id per bundle index]. Lazily defaulted to
  // the bound columns themselves when the user has not overridden a slot.
  const [replaceTargets, setReplaceTargets] = React.useState({});

  function targetFor(paramId, idx) {
    const overrides = replaceTargets[paramId] || [];
    if (overrides[idx] != null) return overrides[idx];
    return (selections[paramId] || [])[idx];  // default: the input column
  }

  function setTarget(paramId, idx, columnId) {
    setReplaceTargets(prev => {
      const current = [...(prev[paramId] || (selections[paramId] || []))];
      current[idx] = columnId;
      return { ...prev, [paramId]: current };
    });
  }

  // A column param (pd.Series, column_backed) or str in "column" mode requires ≥1 column selected
  const requiredParams = params.filter(p =>
    (p.param_kind === "column" && (p.param_type === "pd.Series" || p.param_type === "column_backed")) ||
    (p.param_type === "str" && strModes[p.param_id] === "column")
  );
  const allRequiredFilled = requiredParams.every(p => (selections[p.param_id] || []).length > 0);

  // Equal-length-among-varying guard (slice 3 / §12, ADR-0001). Mirrors the backend
  // attach reject so the user is blocked BEFORE the POST: every VARYING column param
  // (>1 selected column) must share one length N; a single-column param broadcasts
  // (static). Two distinct lengths among varying params is a mismatch — Save is
  // disabled and a readable message names the conflicting counts.
  const varyingLengths = params
    .filter(isColumnMode)
    .map(p => (selections[p.param_id] || []).length)
    .filter(len => len > 1);
  const distinctVaryingLengths = [...new Set(varyingLengths)];
  const hasLengthMismatch = distinctVaryingLengths.length > 1;
  const mismatchCounts = [...distinctVaryingLengths].sort((a, b) => a - b);

  function handleColumnToggle(paramId, columnId) {
    setSelections(prev => {
      const current = prev[paramId] || [];
      if (current.includes(columnId)) {
        return { ...prev, [paramId]: current.filter(id => id !== columnId) };
      } else {
        return { ...prev, [paramId]: [...current, columnId] };
      }
    });
  }

  // Minimal reorder control (#233): swap a selected column with its neighbour.
  // The selected-column order IS the argument-bundle column order — it flows to
  // onSave → bindings → POST/PATCH, where the backend persists alias_map.position.
  // The polished drag pane (slice 7) layers on top of this same ordered array.
  function moveColumn(paramId, index, delta) {
    setSelections(prev => {
      const current = [...(prev[paramId] || [])];
      const target = index + delta;
      if (target < 0 || target >= current.length) return prev;
      [current[index], current[target]] = [current[target], current[index]];
      return { ...prev, [paramId]: current };
    });
  }

  // A param is column-bound when it's a pd.Series/column_backed param, or a str
  // currently in "column" mode.
  function isColumnMode(p) {
    return p.param_type === "pd.Series" || p.param_type === "column_backed" ||
      (p.param_type === "str" && strModes[p.param_id] === "column");
  }

  function handleSave() {
    const bindings = params
      .filter(isColumnMode)
      .map(p => ({ param_id: p.param_id, column_ids: selections[p.param_id] || [] }))
      .filter(b => b.column_ids.length > 0);
    // Build the scalar payload. pd.DataFrame params are auto-filled and never scalar.
    // A param NOT in column mode sends its value — blanks included, so clearing the
    // input clears the persisted override (backend reverts to the Python default).
    // A str that moved INTO column mode sends a blank to clear any stale plain-string
    // value, so the step keeps a single source of truth (the column binding). Without
    // this the old scalar lingers in source_scalar_map after a text→column switch.
    const scalars = {};
    params.forEach(p => {
      if (p.param_type === "pd.DataFrame") return;
      if (isColumnMode(p)) {
        if (p.param_type === "str") scalars[p.param_id] = "";
      } else {
        scalars[p.param_id] = scalarValues[p.param_id] || "";
      }
    });

    // Slice 4 extras: output_mode plus replace-target columns (ordered, bundle i ->
    // target i) or an optional append name. output_targets is sent only in replace
    // mode and only for the single bound column param (one varying param per step).
    const extras = { output_mode: outputMode };
    if (outputMode === "append") {
      if (appendName.trim() !== "") extras.append_name = appendName.trim();
    } else {
      const colParam = params.filter(isColumnMode)
        .find(p => (selections[p.param_id] || []).length > 0);
      if (colParam) {
        const n = (selections[colParam.param_id] || []).length;
        extras.output_targets = Array.from({ length: n }, (_, i) => targetFor(colParam.param_id, i));
      }
    }
    onSave(bindings, scalars, extras);
  }

  const displayName = stepName || "Step";

  // Render one parameter row (scalar input or column selectors).
  function renderParam(p) {
    const isDataFrame = p.param_type === "pd.DataFrame";
    const isMultiCol = p.param_type === "pd.Series" || p.param_type === "column_backed";
    const isStr = p.param_type === "str";
    const strMode = strModes[p.param_id] || "text";
    // scalar input shows for int/float/bool and for a str in plain-string mode
    const isScalar = p.param_kind === "scalar" || (!isDataFrame && !isMultiCol && !(isStr && strMode === "column"));

    return (
      <div key={p.param_id} style={{
        padding: "8px 0",
        borderBottom: "1px solid var(--border-soft)",
        display: "flex", flexDirection: "column", gap: 5,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          <span style={{ fontFamily: "'Geist Mono', monospace", fontSize: 11, color: "var(--text)", fontWeight: 600 }}>
            {p.param_name}
          </span>
          <span style={{
            fontSize: 10, padding: "1px 5px", borderRadius: 99,
            background: "var(--panel-3)", color: "var(--text-3)",
            fontFamily: "'Geist Mono', monospace",
          }}>
            {p.param_type}
          </span>
          {isMultiCol && (
            <span style={{ fontSize: 10, color: "#e05252", fontWeight: 600 }}>required</span>
          )}
        </div>

        {/* str mode toggle */}
        {isStr && (
          <div style={{ display: "flex", gap: 4, marginBottom: 2 }}>
            {["text", "column"].map(mode => (
              <button key={mode} onClick={() => setStrModes(prev => ({ ...prev, [p.param_id]: mode }))} style={{
                fontSize: 10, padding: "2px 8px", borderRadius: 99, cursor: "pointer", fontWeight: 500,
                border: "1px solid " + (strMode === mode ? "var(--accent)" : "var(--border)"),
                background: strMode === mode ? "var(--accent-soft)" : "var(--panel-3)",
                color: strMode === mode ? "var(--accent)" : "var(--text-3)",
              }}>
                {mode === "text" ? "Plain string" : "Column-backed"}
              </button>
            ))}
          </div>
        )}

        {isDataFrame && (
          <span style={{ fontSize: 10, color: "var(--text-4)", fontStyle: "italic" }}>auto (full table)</span>
        )}

        {/* Free-text value input: scalar params (int/float/bool) and a str param in
            "Plain string" mode. Persisted via source_scalar_map (#186/#191). */}
        {isScalar && (
          <input
            type="text"
            placeholder={p.param_type}
            value={scalarValues[p.param_id] || ""}
            onChange={e => setScalarValues(prev => ({ ...prev, [p.param_id]: e.target.value }))}
            style={{
              fontSize: 11, padding: "4px 7px",
              borderRadius: "var(--radius)",
              border: "1px solid var(--border)",
              background: "var(--panel-2)", color: "var(--text)",
              width: "100%", boxSizing: "border-box",
            }}
          />
        )}

        {(isMultiCol || (isStr && strMode === "column")) && (
          <>
          {/* #188: the function description shows in the group header above; here it
              carries as a tooltip so the bare bind hint is never the only context. */}
          <div title={p.function_doc || undefined} style={{ fontSize: 10, color: "var(--text-3)", marginBottom: 3 }}>
            Bind column(s) to <span style={{ fontFamily: "'Geist Mono', monospace", color: "var(--accent)" }}>{p.param_name}</span>:
          </div>
          <div style={{
            display: "flex", flexDirection: "column", gap: 2,
            maxHeight: 120, overflowY: "auto",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            padding: "4px 6px",
            background: "var(--panel-2)",
          }}>
            {availableColumns.length === 0 && (
              <span style={{ fontSize: 10, color: "var(--text-4)", fontStyle: "italic" }}>No columns available.</span>
            )}
            {availableColumns.map(col => {
              const selected = (selections[p.param_id] || []).includes(col.column_id);
              return (
                <div key={col.column_id} onClick={() => handleColumnToggle(p.param_id, col.column_id)}
                  style={{ display: "flex", alignItems: "center", gap: 8,
                    cursor: "pointer", fontSize: 11,
                    color: selected ? "var(--accent)" : "var(--text)",
                    fontWeight: selected ? 600 : 400 }}>
                  {/* #189: the whole row is the click target. The checkbox is
                      presentational (no own onChange) so a click on it does not
                      double-toggle — the row's onClick is the single source of truth. */}
                  <Checkbox checked={selected} />
                  <span className="mono">{col.column_name}</span>
                  <span style={{ fontSize: 10, color: "var(--text-4)" }}>{col.column_type}</span>
                </div>
              );
            })}
          </div>
          {/* #233: minimal reorder control. Shown when ≥2 columns are bound, since
              column order only matters for multi-column argument bundles. Move
              up/down reorders selections[p.param_id], which is the persisted order. */}
          {(selections[p.param_id] || []).length > 1 && (
            <div data-testid={"reorder-" + p.param_id} style={{
              display: "flex", flexDirection: "column", gap: 2, marginTop: 4,
            }}>
              <div style={{ fontSize: 10, color: "var(--text-3)" }}>Column order:</div>
              {(selections[p.param_id] || []).map((colId, idx) => {
                const col = availableColumns.find(c => c.column_id === colId);
                const colName = col ? col.column_name : colId;
                const selCount = (selections[p.param_id] || []).length;
                return (
                  <div key={colId} data-testid="reorder-item" style={{
                    display: "flex", alignItems: "center", gap: 6, fontSize: 11,
                  }}>
                    <span style={{ fontSize: 10, color: "var(--text-4)", width: 14 }}>{idx + 1}.</span>
                    <span className="mono" style={{ flex: 1 }}>{colName}</span>
                    <button aria-label="Move up" disabled={idx === 0}
                      onClick={() => moveColumn(p.param_id, idx, -1)}
                      style={{
                        fontSize: 10, padding: "1px 6px", borderRadius: 99,
                        border: "1px solid var(--border)", background: "var(--panel-3)",
                        color: idx === 0 ? "var(--text-4)" : "var(--text)",
                        cursor: idx === 0 ? "not-allowed" : "pointer",
                      }}>↑</button>
                    <button aria-label="Move down" disabled={idx === selCount - 1}
                      onClick={() => moveColumn(p.param_id, idx, 1)}
                      style={{
                        fontSize: 10, padding: "1px 6px", borderRadius: 99,
                        border: "1px solid var(--border)", background: "var(--panel-3)",
                        color: idx === selCount - 1 ? "var(--text-4)" : "var(--text)",
                        cursor: idx === selCount - 1 ? "not-allowed" : "pointer",
                      }}>↓</button>
                  </div>
                );
              })}
            </div>
          )}
          </>
        )}
      </div>
    );
  }

  // Group params by owning function so each function's params render together,
  // one header per function (#190/#188), preserving first-seen order.
  const paramGroups = [];
  const groupIndex = {};
  params.forEach(p => {
    const key = p.function_name || "";
    if (!(key in groupIndex)) {
      groupIndex[key] = paramGroups.length;
      paramGroups.push({ function_name: p.function_name || "", function_doc: p.function_doc || "", params: [] });
    }
    paramGroups[groupIndex[key]].params.push(p);
  });

  return (
    <div style={{
      background: "var(--panel)",
      border: "2px solid var(--accent)",
      borderRadius: "var(--radius-lg)",
      padding: "12px 14px",
      display: "flex", flexDirection: "column", gap: 10,
      marginTop: 10,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{
          fontSize: 10, fontWeight: 700,
          background: "var(--accent-soft)", color: "var(--accent)",
          borderRadius: 99, padding: "2px 7px", flexShrink: 0,
        }}>
          pending
        </span>
        <span style={{ fontWeight: 600, fontSize: 13, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {displayName}
        </span>
      </div>

      {params.length === 0 && (
        <div style={{ fontSize: 11, color: "var(--text-4)", fontStyle: "italic" }}>No parameters to configure.</div>
      )}

      {paramGroups.map(group => (
        <div key={group.function_name || "_"} data-fn-group={group.function_name || ""} style={{
          display: "flex", flexDirection: "column", gap: 4,
        }}>
          {group.function_name && (
            <div style={{
              fontSize: 10, fontWeight: 700, letterSpacing: ".04em",
              color: "var(--text-3)", textTransform: "uppercase",
              fontFamily: "'Geist Mono', monospace", paddingTop: 4,
            }}>
              {group.function_name}
            </div>
          )}
          {group.function_doc && (
            <div style={{ fontSize: 10, color: "var(--text-4)", lineHeight: 1.5 }}>
              {group.function_doc}
            </div>
          )}
          {group.params.map(p => renderParam(p))}
        </div>
      ))}

      {/* Slice 4 (#241): output-mode + replace-target / append-name controls. */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6, paddingTop: 4, borderTop: "1px solid var(--border-soft)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 11, color: "var(--text-3)" }}>Output:</span>
          <select
            data-testid="output-mode-select"
            value={outputMode}
            onChange={e => setOutputMode(e.target.value)}
            style={{
              fontSize: 11, padding: "2px 6px", borderRadius: "var(--radius)",
              border: "1px solid var(--border)", background: "var(--panel-2)",
              color: "var(--text)", cursor: "pointer",
            }}
          >
            <option value="append">Append</option>
            <option value="replace">Replace</option>
          </select>
        </div>

        {outputMode === "append" && (
          <input
            data-testid="append-name-input"
            type="text"
            placeholder="New column name (optional)"
            value={appendName}
            onChange={e => setAppendName(e.target.value)}
            style={{
              fontSize: 11, padding: "4px 7px", borderRadius: "var(--radius)",
              border: "1px solid var(--border)", background: "var(--panel-2)",
              color: "var(--text)", width: "100%", boxSizing: "border-box",
            }}
          />
        )}

        {outputMode === "replace" && params.filter(isColumnMode).map(p => {
          const cols = selections[p.param_id] || [];
          if (cols.length === 0) return null;
          return (
            <div key={p.param_id} data-testid={"replace-targets-" + p.param_id}
              style={{ display: "flex", flexDirection: "column", gap: 3 }}>
              <div style={{ fontSize: 10, color: "var(--text-3)" }}>
                Replace target column(s) for <span className="mono" style={{ color: "var(--accent)" }}>{p.param_name}</span>:
              </div>
              {cols.map((colId, idx) => {
                const inputCol = availableColumns.find(c => c.column_id === colId);
                const inputName = inputCol ? inputCol.column_name : colId;
                return (
                  <div key={colId} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
                    <span className="mono" style={{ color: "var(--text-4)", flex: 1 }}>{inputName} →</span>
                    <select
                      value={targetFor(p.param_id, idx)}
                      onChange={e => setTarget(p.param_id, idx, e.target.value)}
                      style={{
                        fontSize: 11, padding: "2px 6px", borderRadius: "var(--radius)",
                        border: "1px solid var(--border)", background: "var(--panel-2)",
                        color: "var(--text)", cursor: "pointer", flex: 1,
                      }}
                    >
                      {availableColumns.map(c => (
                        <option key={c.column_id} value={c.column_id}>{c.column_name}</option>
                      ))}
                    </select>
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>

      {hasLengthMismatch && (
        <div data-testid="equal-length-error" style={{ color: "#e05252", fontSize: 11, padding: "4px 0" }}>
          Columns don't line up: varying parameters bind {mismatchCounts.join(" and ")} columns.
          All varying parameters must bind the same number of columns (a single-column
          parameter broadcasts).
        </div>
      )}

      {saveError && (
        <div style={{ color: "#e05252", fontSize: 11, padding: "4px 0" }}>{saveError}</div>
      )}

      <div style={{ display: "flex", gap: 8, paddingTop: 2 }}>
        <button
          onClick={handleSave}
          disabled={!allRequiredFilled || hasLengthMismatch || saving}
          style={{
            flex: 1, padding: "7px 0", fontSize: 12, fontWeight: 600,
            borderRadius: "var(--radius)",
            border: "none",
            background: allRequiredFilled && !hasLengthMismatch && !saving ? "var(--accent)" : "var(--panel-3)",
            color: allRequiredFilled && !hasLengthMismatch && !saving ? "#fff" : "var(--text-4)",
            cursor: allRequiredFilled && !hasLengthMismatch && !saving ? "pointer" : "not-allowed",
            transition: "background .15s",
          }}
        >
          {saving ? "Saving..." : "Save"}
        </button>
        <button
          onClick={onCancel}
          disabled={saving}
          style={{
            flex: 1, padding: "7px 0", fontSize: 12, fontWeight: 600,
            borderRadius: "var(--radius)",
            border: "1px solid var(--border)",
            background: "var(--panel-2)", color: "var(--text)",
            cursor: saving ? "not-allowed" : "pointer",
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step card
// ---------------------------------------------------------------------------

function StepCard({ step, sourceId, order, onRemoved, isDragging, onDragStart, onDragEnd, onDragOver, resultTag, onNavigateResults, onEdit }) {
  const { OrderBadge } = window.__UI__;
  const setType = deriveSetType(step.functions);
  const badgeStyle = TYPE_BADGE_COLORS[setType] || TYPE_BADGE_COLORS.Unknown;
  const [removing, setRemoving] = useState(false);
  const [outputMode, setOutputMode] = useState(step.output_mode || "append");
  const showOutputMode = setType === "Transform" || setType === "Mixed";

  function handleRemove() {
    if (removing) return;
    setRemoving(true);
    fetch("/pipelines/" + sourceId + "/steps/" + step.source_function_map_id, { method: "DELETE" })
      .then(r => { if (r.ok || r.status === 204) onRemoved(); else setRemoving(false); })
      .catch(() => setRemoving(false));
  }

  function handleOutputModeChange(e) {
    const newMode = e.target.value;
    const prev = outputMode;
    setOutputMode(newMode);
    fetch("/pipelines/" + sourceId + "/steps/" + step.source_function_map_id, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ output_mode: newMode }),
    }).catch(() => setOutputMode(prev));
  }

  return (
    <div
      onDragOver={onDragOver}
      style={{
        background: "var(--panel)", border: "1px solid var(--border)",
        borderRadius: "var(--radius-lg)", padding: "12px 14px",
        display: "flex", flexDirection: "column", gap: 8,
        opacity: isDragging ? 0.4 : 1,
        transition: "opacity .15s",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
        <OrderBadge n={order} dragging={isDragging} draggable onDragStart={onDragStart} onDragEnd={onDragEnd} />
        <span style={{ fontWeight: 600, fontSize: 13, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {step.set_name}
        </span>
        <span style={{
          fontSize: 10, fontWeight: 600, padding: "2px 8px", borderRadius: 99, flexShrink: 0,
          background: badgeStyle.bg, color: badgeStyle.color,
        }}>
          {setType}
        </span>
        {resultTag && (
          <button
            onClick={() => onNavigateResults && onNavigateResults()}
            title={"Result: " + resultTag + " — click to view results"}
            style={{
              fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 99, flexShrink: 0,
              background: RESULT_TAG_STYLES[resultTag].bg,
              color: RESULT_TAG_STYLES[resultTag].color,
              border: "none", cursor: "pointer",
            }}
          >
            {resultTag}
          </button>
        )}
        <button
          onClick={() => onEdit && onEdit(step)}
          title="Edit step bindings"
          style={{
            background: "none", border: "none", cursor: "pointer",
            color: "var(--text-4)", fontSize: 14, lineHeight: 1, padding: "2px 4px",
            borderRadius: "var(--radius)", flexShrink: 0,
          }}
          onMouseEnter={e => { e.currentTarget.style.color = "var(--accent)"; }}
          onMouseLeave={e => { e.currentTarget.style.color = "var(--text-4)"; }}
        >
          ✎
        </button>
        <button
          onClick={handleRemove}
          disabled={removing}
          title="Remove step"
          style={{
            background: "none", border: "none", cursor: removing ? "default" : "pointer",
            color: "var(--text-4)", fontSize: 16, lineHeight: 1, padding: "2px 4px",
            borderRadius: "var(--radius)", opacity: removing ? 0.4 : 1, flexShrink: 0,
          }}
          onMouseEnter={e => { if (!removing) e.currentTarget.style.color = "var(--danger, #e55)"; }}
          onMouseLeave={e => { e.currentTarget.style.color = "var(--text-4)"; }}
        >
          x
        </button>
      </div>

      {step.functions.map(fn => (
        <FunctionRow key={fn.function_id} fn={fn} />
      ))}

      {showOutputMode && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, paddingTop: 4 }}>
          <span style={{ fontSize: 11, color: "var(--text-3)", flexShrink: 0 }}>Output:</span>
          <select
            value={outputMode}
            onChange={handleOutputModeChange}
            style={{
              fontSize: 11, padding: "2px 6px", borderRadius: "var(--radius)",
              border: "1px solid var(--border)", background: "var(--panel-2)",
              color: "var(--text)", cursor: "pointer",
            }}
          >
            <option value="append">Append</option>
            <option value="replace">Replace</option>
          </select>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Built-in step card — a placed join/pivot/filter step on the canvas (#209).
//
// Visually distinct from function/set cards: it pins the same permanent accent
// left-strip + "built-in" tag the palette built-in card uses (PaletteBuiltinCard),
// and shows a one-line config summary derived from builtin_config. Identified,
// removed, and edited by step_id via the built-in routes (DELETE/PATCH
// /sources/{id}/attach-builtin/{step_id}). Non-draggable — reorder is out of scope.
// ---------------------------------------------------------------------------

// Compose a one-line summary from a built-in step's config. For join:
//   "Join · <right source> · <join_type> · N key(s)".
// `sources` is the source list used to resolve right_source_id → source_name;
// it falls back to the raw id when the name isn't known.
function builtinConfigSummary(step, sources) {
  const cfg = step.builtin_config || {};
  if (step.builtin_type === "join") {
    const rid = cfg.right_source_id;
    const match = (sources || []).find(s => s.source_id === rid);
    const rightName = (match && match.source_name) || rid || "?";
    const joinType = cfg.join_type || "inner";
    const nKeys = Array.isArray(cfg.on) ? cfg.on.length : 0;
    return `Join · ${rightName} · ${joinType} · ${nKeys} ${nKeys === 1 ? "key" : "keys"}`;
  }
  if (step.builtin_type === "filter") {
    const col = cfg.column || "?";
    const op = cfg.operator || "?";
    const NULLARY = { is_null: "is null", is_not_null: "is not null" };
    if (NULLARY[op]) return `Filter · ${col} ${NULLARY[op]}`;
    const SYM = {
      eq: "=", neq: "≠", gt: ">", gte: "≥", lt: "<", lte: "≤",
      contains: "contains", not_contains: "not contains",
    };
    const opStr = SYM[op] || op;
    const val = cfg.value != null ? String(cfg.value) : "";
    return `Filter · ${col} ${opStr} ${val}`.trim();
  }
  if (step.builtin_type === "rename") {
    const entries = Object.entries(cfg.renames || {});
    if (!entries.length) return "Rename";
    const shown = entries.slice(0, 3).map(([o, n]) => `${o}→${n}`).join(", ");
    const more = entries.length > 3 ? ` +${entries.length - 3}` : "";
    return `Rename · ${shown}${more}`;
  }
  // Generic fallback for other built-in types (only join + filter + rename are wired).
  const label = step.builtin_type ? step.builtin_type.charAt(0).toUpperCase() + step.builtin_type.slice(1) : "Step";
  return label;
}

function BuiltinStepCard({ step, sourceId, order, sources, onRemoved, onEdit }) {
  const { OrderBadge } = window.__UI__;
  const [removing, setRemoving] = useState(false);
  const summary = builtinConfigSummary(step, sources);

  function handleRemove() {
    if (removing) return;
    setRemoving(true);
    fetch("/sources/" + sourceId + "/attach-builtin/" + step.step_id, { method: "DELETE" })
      .then(r => { if (r.ok || r.status === 204) onRemoved(); else setRemoving(false); })
      .catch(() => setRemoving(false));
  }

  return (
    <div
      style={{
        position: "relative",
        background: "var(--panel)",
        borderStyle: "solid", borderWidth: 1, borderColor: "var(--border)",
        borderRadius: "var(--radius-lg)", padding: "12px 14px",
        // Permanent accent left-strip — the built-in visual signature.
        boxShadow: "inset 2px 0 0 0 var(--accent)",
        display: "flex", flexDirection: "column", gap: 6,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
        <OrderBadge n={order} />
        <span style={{ fontWeight: 600, fontSize: 13, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {step.builtin_type ? (step.builtin_type.charAt(0).toUpperCase() + step.builtin_type.slice(1)) : "Built-in"}
        </span>
        <span style={{
          fontSize: 9, fontWeight: 700, letterSpacing: ".04em", textTransform: "uppercase",
          padding: "1px 5px", borderRadius: 99, flexShrink: 0,
          background: "var(--accent-soft)", color: "var(--accent)",
          border: "1px solid var(--accent-line)",
          fontFamily: "'Geist Mono', monospace",
        }}>
          built-in
        </span>
        <button
          onClick={() => onEdit && onEdit(step)}
          title="Edit step"
          style={{
            background: "none", border: "none", cursor: "pointer",
            color: "var(--text-4)", fontSize: 14, lineHeight: 1, padding: "2px 4px",
            borderRadius: "var(--radius)", flexShrink: 0,
          }}
          onMouseEnter={e => { e.currentTarget.style.color = "var(--accent)"; }}
          onMouseLeave={e => { e.currentTarget.style.color = "var(--text-4)"; }}
        >
          ✎
        </button>
        <button
          onClick={handleRemove}
          disabled={removing}
          title="Remove step"
          style={{
            background: "none", border: "none", cursor: removing ? "default" : "pointer",
            color: "var(--text-4)", fontSize: 16, lineHeight: 1, padding: "2px 4px",
            borderRadius: "var(--radius)", opacity: removing ? 0.4 : 1, flexShrink: 0,
          }}
          onMouseEnter={e => { if (!removing) e.currentTarget.style.color = "var(--danger, #e55)"; }}
          onMouseLeave={e => { e.currentTarget.style.color = "var(--text-4)"; }}
        >
          x
        </button>
      </div>
      <div className="mono" style={{ fontSize: 11, color: "var(--text-3)", paddingLeft: 2 }}>
        {summary}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pipeline canvas — drag-to-reorder
// ---------------------------------------------------------------------------

function PipelineCanvas({ sourceId, steps, sources, onReloadPipeline, resultTags, onNavigateResults, onEditStep, onEditBuiltin }) {
  const [localSteps, setLocalSteps] = useState(steps);
  const dragIndexRef = useRef(null);

  useEffect(() => { setLocalSteps(steps); }, [steps]);

  function handleDragStart(index) {
    dragIndexRef.current = index;
  }

  function handleDragEnd() {
    dragIndexRef.current = null;
    // Reload to get authoritative server order
    onReloadPipeline();
  }

  function handleDragOver(e, overIndex) {
    e.preventDefault();
    const fromIndex = dragIndexRef.current;
    if (fromIndex === null || fromIndex === overIndex) return;

    const reordered = [...localSteps];
    const [moved] = reordered.splice(fromIndex, 1);
    reordered.splice(overIndex, 0, moved);
    dragIndexRef.current = overIndex;
    setLocalSteps(reordered);

    // PATCH the moved step — use the target step's position value
    const targetStep = steps[overIndex];
    const newPosition = targetStep ? targetStep.position : overIndex;
    fetch("/pipelines/" + sourceId + "/steps/" + moved.source_function_map_id, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ position: newPosition }),
    }).catch(() => onReloadPipeline());
  }

  if (localSteps.length === 0) {
    return (
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "center",
        color: "var(--text-4)", fontSize: 13, paddingTop: 40, paddingBottom: 40,
      }}>
        No pipeline steps yet.
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {localSteps.map((step, index) => {
        // Dispatch by discriminator. A step with no step_type is treated as a
        // function step for backward safety (#209).
        if (step.step_type === "builtin") {
          return (
            <BuiltinStepCard
              key={step.step_id}
              step={step}
              sourceId={sourceId}
              order={index + 1}
              sources={sources}
              onRemoved={onReloadPipeline}
              onEdit={onEditBuiltin}
            />
          );
        }
        return (
          <StepCard
            key={step.source_function_map_id}
            step={step}
            sourceId={sourceId}
            order={index + 1}
            onRemoved={onReloadPipeline}
            isDragging={dragIndexRef.current === index}
            onDragStart={() => handleDragStart(index)}
            onDragEnd={handleDragEnd}
            onDragOver={e => handleDragOver(e, index)}
            resultTag={resultTags && resultTags[step.source_function_map_id]}
            onNavigateResults={onNavigateResults}
            onEdit={onEditStep}
          />
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Right palette — Functions + Sets + Built-ins tabs
// ---------------------------------------------------------------------------

// Read-only function drawer used from the palette (no run/attach controls)
function PaletteFunctionDrawer({ functionId, onClose, flash }) {
  const { Drawer, KindTag, StatusPill } = window.__UI__;
  const [detail, setDetail] = useState(null);

  useEffect(() => {
    setDetail(null);
    if (!functionId) return;
    fetch(`/functions/${functionId}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => setDetail(d))
      .catch(() => flash && flash("Could not load function detail.", "error"));
  }, [functionId]);

  if (!functionId) return null;
  const fn = detail;

  return (
    <Drawer open={!!functionId} onClose={onClose} title={fn?.function_name ?? "…"} width={440}>
      {fn && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <KindTag kind={fn.function_type || "transform"} />
            <StatusPill status={fn.is_active ? "active" : "inactive"} />
            <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-4)", fontFamily: "'Geist Mono', monospace" }}>
              {fn.function_class}
            </span>
          </div>
          {fn.function_doc && (
            <div style={{ fontSize: 13, color: "var(--text-2)", lineHeight: 1.6 }}>
              {fn.function_doc}
            </div>
          )}
          <div style={{
            fontFamily: "'Geist Mono', monospace", fontSize: 12,
            background: "var(--panel-2)", border: "1px solid var(--border)",
            borderRadius: "var(--radius)", padding: "8px 12px", color: "var(--text)",
          }}>
            {fn.function_name}{fn.function_signature}
          </div>
          {fn.parameters && fn.parameters.length > 0 && (
            <div>
              <div style={{ fontSize: 11, color: "var(--text-3)", fontWeight: 600, letterSpacing: ".05em", textTransform: "uppercase", marginBottom: 8 }}>
                Parameters ({fn.parameters.length})
              </div>
              {fn.parameters.map((p, i) => (
                <div key={p.param_id} style={{
                  display: "flex", justifyContent: "space-between", padding: "5px 0",
                  borderBottom: "1px solid var(--border-soft)", fontSize: 12,
                  fontFamily: "'Geist Mono', monospace",
                }}>
                  <span style={{ color: "var(--text)" }}>{p.param_name}</span>
                  <span style={{ color: "var(--text-3)" }}>{p.param_type}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Drawer>
  );
}

function PaletteFunctionCard({ fn, onDragStart, onOpenDrawer }) {
  return (
    <div
      draggable
      onDragStart={e => {
        e.dataTransfer.setData("palette/function_id", fn.function_id);
        e.dataTransfer.setData("palette/function_name", fn.function_name);
        if (onDragStart) onDragStart();
      }}
      onClick={e => { if (onOpenDrawer) onOpenDrawer(fn.function_id); }}
      title={fn.function_doc || fn.function_name}
      style={{
        padding: "7px 10px",
        borderRadius: "var(--radius)",
        border: "1px solid var(--border)",
        background: "var(--panel)",
        cursor: "pointer",
        marginBottom: 5,
      }}
    >
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {fn.function_name}
      </div>
      <div style={{ display: "flex", gap: 4, marginTop: 3 }}>
        <span style={{
          fontSize: 10, padding: "1px 5px", borderRadius: 99,
          background: "var(--panel-3)", color: "var(--text-3)",
          fontFamily: "'Geist Mono', monospace",
        }}>{fn.function_class}</span>
      </div>
      {fn.function_doc && (
        <div style={{ fontSize: 10, color: "var(--text-4)", marginTop: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {fn.function_doc}
        </div>
      )}
    </div>
  );
}

function PaletteSetCard({ set, onDragStart, onOpenDrawer }) {
  const setType = deriveSetType(set.functions || []);
  const badgeStyle = TYPE_BADGE_COLORS[setType] || TYPE_BADGE_COLORS.Unknown;
  return (
    <div
      draggable
      onDragStart={e => {
        e.dataTransfer.setData("palette/set_id", set.set_id);
        e.dataTransfer.setData("palette/set_name", set.set_name);
        if (onDragStart) onDragStart();
      }}
      onClick={() => { if (onOpenDrawer) onOpenDrawer(set.set_id); }}
      title={set.set_description || set.set_name}
      style={{
        padding: "7px 10px",
        borderRadius: "var(--radius)",
        border: "1px solid var(--border)",
        background: "var(--panel)",
        cursor: "pointer",
        marginBottom: 5,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
        <span style={{ fontSize: 12, fontWeight: 600, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {set.set_name}
        </span>
        <span style={{
          fontSize: 10, fontWeight: 600, padding: "1px 6px", borderRadius: 99, flexShrink: 0,
          background: badgeStyle.bg, color: badgeStyle.color,
        }}>
          {setType}
        </span>
      </div>
      {set.set_description && (
        <div style={{ fontSize: 10, color: "var(--text-4)", marginTop: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {set.set_description}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SetDetailDrawer — read-only drawer for a function set
// ---------------------------------------------------------------------------

function SetDetailDrawer({ setId, onClose, flash }) {
  const { Drawer, KindTag, StatusPill } = window.__UI__;
  const [detail, setDetail] = useState(null);

  useEffect(() => {
    setDetail(null);
    if (!setId) return;
    fetch(`/function-sets/${setId}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => setDetail(d))
      .catch(() => flash && flash("Could not load set detail.", "error"));
  }, [setId]);

  if (!setId) return null;

  const hasInactive = detail && detail.members && detail.members.some(m => !m.is_active);

  return (
    <Drawer open={!!setId} onClose={onClose} title={detail?.set_name ?? "…"} width={440}>
      {detail && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {detail.set_description && (
            <div style={{ fontSize: 13, color: "var(--text-2)", lineHeight: 1.6 }}>
              {detail.set_description}
            </div>
          )}
          {hasInactive && (
            <div style={{
              background: "var(--warn-soft, rgba(232,160,32,.12))",
              border: "1px solid var(--warn, #e8a020)",
              borderRadius: "var(--radius)", padding: "10px 14px",
              fontSize: 12, color: "var(--warn, #e8a020)", fontWeight: 500,
            }}>
              This set contains inactive functions and may produce incomplete results.
            </div>
          )}
          <div>
            <div style={{
              fontSize: 11, color: "var(--text-3)", fontWeight: 600,
              letterSpacing: ".05em", textTransform: "uppercase", marginBottom: 8,
            }}>
              Members ({detail.members?.length ?? 0})
            </div>
            {(detail.members || []).map((m, i) => (
              <div key={m.function_id} style={{
                display: "flex", alignItems: "center", gap: 8,
                padding: "8px 0", borderBottom: "1px solid var(--border-soft)",
                opacity: m.is_active ? 1 : 0.5,
              }}>
                <span style={{ fontSize: 11, color: "var(--text-4)", minWidth: 18, textAlign: "right" }}>{i + 1}</span>
                <KindTag kind={m.function_type || "transform"} />
                <span style={{ fontSize: 13, flex: 1 }}>{m.function_name}</span>
                <StatusPill status={m.is_active ? "active" : "inactive"} />
              </div>
            ))}
          </div>
        </div>
      )}
    </Drawer>
  );
}

// ---------------------------------------------------------------------------
// PaletteCardShell — shared hover/drag shell for ALL palette card types.
// All-longhand borders + inset box-shadow (no shorthand conflicts, animates
// cleanly). `accentEdge` pins a permanent 2px --accent left-strip (built-ins).
// ---------------------------------------------------------------------------

function PaletteCardShell({ accentEdge, draggable, onDragStart, onClick, title, children }) {
  const [hot, setHot] = useState(false);
  const edge = accentEdge
    ? "inset 2px 0 0 0 var(--accent)"
    : hot
      ? "inset 2px 0 0 0 var(--accent-line)"
      : "inset 0 0 0 0 transparent";
  return (
    <div
      draggable={draggable}
      onDragStart={onDragStart}
      onClick={onClick}
      title={title}
      onMouseEnter={() => setHot(true)}
      onMouseLeave={() => setHot(false)}
      style={{
        position: "relative",
        padding: "7px 10px",
        borderStyle: "solid",
        borderWidth: 1,
        borderColor: hot ? "var(--accent-line)" : "var(--border)",
        borderRadius: "var(--radius)",
        background: hot ? "var(--accent-soft)" : "var(--panel)",
        boxShadow: edge,
        cursor: draggable ? "grab" : "pointer",
        marginBottom: 5,
        transition: "background .12s, border-color .12s, box-shadow .12s",
      }}
    >
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Read-only minimal detail drawer for a builtin, opened from the palette (#256).
// Mirrors PaletteFunctionDrawer/SetDetailDrawer and reuses window.__UI__.Drawer —
// no new primitive. Renders straight from the already-loaded /builtins payload
// (display_name, builtin_type, description, config_schema) — no fetch needed.
function PaletteBuiltinDrawer({ builtin, onClose }) {
  const { Drawer } = window.__UI__;
  if (!builtin) return null;
  const schema = builtin.config_schema || {};
  const keys = Object.keys(schema);
  return (
    <Drawer open={!!builtin} onClose={onClose} title={builtin.display_name || builtin.builtin_type} width={440}>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{
            fontSize: 9, fontWeight: 700, letterSpacing: ".04em", textTransform: "uppercase",
            padding: "1px 5px", borderRadius: 99,
            background: "var(--accent-soft)", color: "var(--accent)", border: "1px solid var(--accent-line)",
            fontFamily: "'Geist Mono', monospace",
          }}>
            built-in
          </span>
          <span style={{ fontSize: 11, color: "var(--text-3)", fontFamily: "'Geist Mono', monospace" }}>
            {builtin.builtin_type}
          </span>
        </div>
        {builtin.description && (
          <div style={{ fontSize: 13, color: "var(--text-2)", lineHeight: 1.6 }}>
            {builtin.description}
          </div>
        )}
        {keys.length > 0 && (
          <div>
            <div style={{ fontSize: 11, color: "var(--text-3)", fontWeight: 600, letterSpacing: ".05em", textTransform: "uppercase", marginBottom: 8 }}>
              Config ({keys.length})
            </div>
            {keys.map(k => (
              <div key={k} style={{
                display: "flex", justifyContent: "space-between", padding: "5px 0",
                borderBottom: "1px solid var(--border-soft)", fontSize: 12,
                fontFamily: "'Geist Mono', monospace",
              }}>
                <span style={{ color: "var(--text)" }}>{k}</span>
                <span style={{ color: "var(--text-3)" }}>
                  {typeof schema[k] === "object" ? JSON.stringify(schema[k]) : String(schema[k])}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </Drawer>
  );
}

// PaletteBuiltinCard — draggable card for a builtin step. Visually distinct via
// the permanent accent edge + a "built-in" tag next to the name. Clicking opens
// a read-only PaletteBuiltinDrawer (#256), mirroring function/set cards.
// ---------------------------------------------------------------------------

function PaletteBuiltinCard({ builtin, onDragStart, onOpenDrawer }) {
  return (
    <PaletteCardShell
      accentEdge
      draggable
      onDragStart={e => {
        e.dataTransfer.setData("palette/builtin_type", builtin.builtin_type);
        e.dataTransfer.setData("palette/builtin_label", builtin.display_name || builtin.builtin_type);
        if (onDragStart) onDragStart();
      }}
      onClick={() => { if (onOpenDrawer) onOpenDrawer(builtin.builtin_type); }}
      title={builtin.description || builtin.display_name}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
        <span style={{ fontSize: 12, fontWeight: 600, flex: 1, color: "var(--text)" }}>
          {builtin.display_name}
        </span>
        <span style={{
          fontSize: 9, fontWeight: 700, letterSpacing: ".04em", textTransform: "uppercase",
          padding: "1px 5px", borderRadius: 99, flexShrink: 0,
          background: "var(--accent-soft)", color: "var(--accent)",
          border: "1px solid var(--accent-line)",
          fontFamily: "'Geist Mono', monospace",
        }}>
          built-in
        </span>
      </div>
      {builtin.description && (
        <div style={{ fontSize: 10, color: "var(--text-4)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {builtin.description.split(".")[0]}
        </div>
      )}
    </PaletteCardShell>
  );
}

function RightPalette({ selectedSource, flash }) {
  const { LoadingState } = window.__UI__;
  const [activeTab, setActiveTab] = useState("functions");
  const [functions, setFunctions] = useState([]);
  const [sets, setSets] = useState([]);
  const [setsDetail, setSetsDetail] = useState({});
  const [builtins, setBuiltins] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectedFunctionId, setSelectedFunctionId] = useState(null);
  const [selectedSetId, setSelectedSetId] = useState(null);
  const [selectedBuiltinId, setSelectedBuiltinId] = useState(null);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      fetch("/functions").then(r => r.ok ? r.json() : []),
      fetch("/function-sets").then(r => r.ok ? r.json() : []),
      fetch("/builtins").then(r => r.ok ? r.json() : []),
    ]).then(([fns, sts, bts]) => {
      setFunctions(Array.isArray(fns) ? fns : []);
      setSets(Array.isArray(sts) ? sts : []);
      setBuiltins(Array.isArray(bts) ? bts : []);
      setLoading(false);
      (Array.isArray(sts) ? sts : []).forEach(s => {
        fetch("/function-sets/" + s.set_id)
          .then(r => r.ok ? r.json() : null)
          .then(detail => {
            if (detail) setSetsDetail(prev => ({ ...prev, [s.set_id]: detail }));
          })
          .catch(() => {});
      });
    }).catch(() => setLoading(false));
  }, []);

  const validationFns = functions.filter(f => f.function_type === "validation");
  const transformFns = functions.filter(f => f.function_type === "transform");
  const setsWithDetail = sets.map(s => ({
    ...s,
    functions: (setsDetail[s.set_id] && setsDetail[s.set_id].functions) || [],
  }));
  // Filter single-member sets from palette
  const multiMemberSets = setsWithDetail.filter(s => (s.member_count ?? s.function_count) !== 1);

  const tabStyle = (tab) => ({
    flex: 1, padding: "6px 0", fontSize: 11, fontWeight: 600,
    background: "none", border: "none",
    borderBottom: activeTab === tab ? "2px solid var(--accent)" : "2px solid transparent",
    color: activeTab === tab ? "var(--accent)" : "var(--text-3)",
    cursor: "pointer",
    letterSpacing: ".03em",
  });

  return (
    <div style={{
      width: 220, flexShrink: 0,
      borderLeft: "1px solid var(--border)",
      display: "flex", flexDirection: "column",
      overflow: "hidden",
    }}>
      <div style={{ display: "flex", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
        <button style={tabStyle("functions")} onClick={() => setActiveTab("functions")}>Functions</button>
        <button style={tabStyle("sets")} onClick={() => setActiveTab("sets")}>Sets</button>
        <button style={tabStyle("builtins")} onClick={() => setActiveTab("builtins")}>Built-ins</button>
      </div>
      <div style={{ flex: 1, overflow: "auto", padding: "10px 10px 10px" }}>
        {loading && <LoadingState />}
        {!loading && activeTab === "functions" && (
          <>
            {validationFns.length > 0 && (
              <>
                <div style={{ fontSize: 10, fontWeight: 700, color: "var(--text-3)", letterSpacing: ".06em", textTransform: "uppercase", marginBottom: 6 }}>Validation</div>
                {validationFns.map(fn => <PaletteFunctionCard key={fn.function_id} fn={fn} onOpenDrawer={id => setSelectedFunctionId(id)} />)}
              </>
            )}
            {transformFns.length > 0 && (
              <>
                <div style={{ fontSize: 10, fontWeight: 700, color: "var(--text-3)", letterSpacing: ".06em", textTransform: "uppercase", marginBottom: 6, marginTop: validationFns.length > 0 ? 10 : 0 }}>Transform</div>
                {transformFns.map(fn => <PaletteFunctionCard key={fn.function_id} fn={fn} onOpenDrawer={id => setSelectedFunctionId(id)} />)}
              </>
            )}
            {functions.length === 0 && (
              <div style={{ fontSize: 11, color: "var(--text-4)", textAlign: "center", paddingTop: 20 }}>No functions registered.</div>
            )}
          </>
        )}
        {!loading && activeTab === "sets" && (
          <>
            {multiMemberSets.length === 0 ? (
              <div style={{ fontSize: 11, color: "var(--text-4)", textAlign: "center", paddingTop: 20 }}>
                {setsWithDetail.length > 0
                  ? "No multi-function sets yet. Sets are created automatically when you drag multiple functions onto a pipeline."
                  : "No function sets."}
              </div>
            ) : (
              multiMemberSets.map(s => <PaletteSetCard key={s.set_id} set={s} onOpenDrawer={id => setSelectedSetId(id)} />)
            )}
          </>
        )}
        {!loading && activeTab === "builtins" && (
          <>
            {builtins.length === 0 ? (
              <div style={{ fontSize: 11, color: "var(--text-4)", textAlign: "center", paddingTop: 20 }}>No built-ins available.</div>
            ) : (
              builtins.map(b => <PaletteBuiltinCard key={b.builtin_type} builtin={b} onOpenDrawer={t => setSelectedBuiltinId(t)} />)
            )}
          </>
        )}
      </div>
      {/* Function detail drawer (read-only from palette) */}
      {selectedFunctionId && (
        <PaletteFunctionDrawer functionId={selectedFunctionId} onClose={() => setSelectedFunctionId(null)} flash={flash} />
      )}
      {selectedSetId && (
        <SetDetailDrawer setId={selectedSetId} onClose={() => setSelectedSetId(null)} flash={flash} />
      )}
      {selectedBuiltinId && (
        <PaletteBuiltinDrawer
          builtin={builtins.find(b => b.builtin_type === selectedBuiltinId)}
          onClose={() => setSelectedBuiltinId(null)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// JoinModal — two-step modal for configuring a built-in join step (#152)
//
// Pure-ish component: data + async seams are injected so it is fully testable.
//   currentSource:     { source_id, source_name, columns: [{column_id,column_name,column_type}] }
//   sources:           all registered sources (the current one is filtered out)
//   fetchRightColumns: (rightSourceId, useTransformed) => Promise<columns[]>
//   onSubmit:          (builtin_config) => Promise<{ ok, step_id?, detail? }>
// The SidePanel owns the real /sources fetch and the /sources/{id}/attach-builtin POST.
// ---------------------------------------------------------------------------

const JOIN_TYPE_OPTIONS = [
  { value: "inner", hint: "Only rows that match in both." },
  { value: "left", hint: "All left rows; nulls where no match." },
  { value: "right", hint: "All right rows; nulls where no match." },
  { value: "full", hint: "All rows from both sides." },
];

function StepRail({ step }) {
  const { Icon } = window.__UI__;
  const dot = (n, label) => {
    const current = step === n;
    const done = step > n;
    return (
      <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
        <span style={{
          width: 16, height: 16, borderRadius: "50%", display: "inline-flex",
          alignItems: "center", justifyContent: "center", fontSize: 10, fontWeight: 700,
          background: current ? "var(--accent)" : "transparent",
          color: current ? "var(--accent-ink)" : done ? "var(--accent)" : "var(--text-4)",
          border: "1px solid " + (current ? "transparent" : done ? "var(--accent-line)" : "var(--border)"),
        }}>
          {done ? <Icon name="check" size={10} /> : n}
        </span>
        <span style={{ fontSize: 11, color: current ? "var(--accent)" : "var(--text-4)" }}>{label}</span>
      </span>
    );
  };
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8, marginRight: 4 }}>
      {dot(1, "Source")}
      <span style={{ color: "var(--text-4)", fontSize: 11 }}>·</span>
      {dot(2, "Keys")}
    </span>
  );
}

function SourceRow({ source, selected, onSelect, useTransformed, onToggleTransformed }) {
  const { SourceBadge, Switch } = window.__UI__;
  const rows = source.row_count;
  const cols = (source.columns && source.columns.length) != null
    ? (source.columns ? source.columns.length : source.col_count)
    : source.col_count;
  const steps = source.steps || 0;
  const parts = [];
  if (rows != null) parts.push(`${rows} rows`);
  if (cols != null) parts.push(`${cols} cols`);
  if (steps > 0) parts.push(`${steps} pipeline steps`);
  return (
    <div
      data-testid={`source-row-${source.source_id}`}
      onClick={() => onSelect(source.source_id)}
      style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "9px 12px", marginBottom: 6, cursor: "pointer",
        borderRadius: "var(--radius)",
        border: "1px solid " + (selected ? "var(--accent)" : "var(--border)"),
        background: selected ? "var(--accent-soft)" : "var(--panel)",
        transition: "background .1s, border-color .1s",
      }}
    >
      <SourceBadge name={source.source_name} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 13, fontWeight: selected ? 600 : 500,
          color: selected ? "var(--accent)" : "var(--text)",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          {source.source_name}
        </div>
        <div className="mono" style={{ fontSize: 11, color: "var(--text-4)", marginTop: 2 }}>
          {parts.join(" · ")}
        </div>
      </div>
      {steps > 0 && (
        <div
          onClick={e => e.stopPropagation()}
          style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 3 }}
        >
          <Switch checked={!!useTransformed} onChange={v => onToggleTransformed(source.source_id, v)} size="sm" />
          <span style={{ fontSize: 10, color: "var(--text-4)" }}>
            {useTransformed ? "transformed" : "raw columns"}
          </span>
        </div>
      )}
    </div>
  );
}

function ColumnSelectList({ testId, label, columns, selectedName, onSelect, loading }) {
  const { Icon, Spinner } = window.__UI__;
  const [filter, setFilter] = useState("");
  const shown = (columns || []).filter(c =>
    !filter || c.column_name.toLowerCase().includes(filter.toLowerCase())
  );
  return (
    <div data-testid={testId} style={{ flex: 1, minWidth: 0 }}>
      <div style={{
        fontSize: 11, fontWeight: 600, color: "var(--text-3)", marginBottom: 5,
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
      }}>
        {label}
      </div>
      <div style={{
        display: "flex", alignItems: "center", gap: 5, marginBottom: 5,
        padding: "3px 7px", border: "1px solid var(--border)", borderRadius: "var(--radius)",
        background: "var(--panel-2)",
      }}>
        <Icon name="search" size={12} style={{ color: "var(--text-4)" }} />
        <input
          value={filter}
          onChange={e => setFilter(e.target.value)}
          placeholder="Filter columns…"
          style={{
            flex: 1, border: "none", background: "transparent", outline: "none",
            color: "var(--text)", fontSize: 12,
          }}
        />
      </div>
      <div style={{
        maxHeight: 180, overflowY: "auto",
        border: "1px solid var(--border)", borderRadius: "var(--radius)",
      }}>
        {loading ? (
          <div style={{ padding: 12, display: "flex", justifyContent: "center" }}><Spinner /></div>
        ) : shown.length === 0 ? (
          <div style={{ padding: 10, fontSize: 11, color: "var(--text-4)", textAlign: "center" }}>No columns.</div>
        ) : (
          shown.map(c => {
            const isSel = selectedName === c.column_name;
            return (
              <div
                key={c.column_id || c.column_name}
                onClick={() => onSelect(c.column_name)}
                style={{
                  display: "flex", alignItems: "center", gap: 7, padding: "6px 9px", cursor: "pointer",
                  background: isSel ? "var(--accent-soft)" : "transparent",
                  borderBottom: "1px solid var(--border-soft)",
                }}
              >
                <span style={{
                  width: 12, height: 12, borderRadius: "50%", flexShrink: 0,
                  border: "1px solid " + (isSel ? "var(--accent)" : "var(--border)"),
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                }}>
                  {isSel && <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--accent)" }} />}
                </span>
                <span className="mono" style={{
                  flex: 1, fontSize: 12, color: isSel ? "var(--accent)" : "var(--text)",
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>
                  {c.column_name}
                </span>
                {c.column_type && (
                  <span style={{
                    fontSize: 9, padding: "1px 5px", borderRadius: 99, flexShrink: 0,
                    background: "var(--panel-3)", color: "var(--text-4)",
                    fontFamily: "'Geist Mono', monospace",
                  }}>
                    {c.column_type}
                  </span>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

function KeyPairBuilder({ pairs, setPairs, leftSource, rightSource, leftColumns, rightColumns, rightLoading }) {
  const { Icon } = window.__UI__;
  const composite = pairs.length > 1;
  // Functional updates: two selections coalesced into one React batch must each see
  // the prior update, not a stale `pairs` snapshot (else the second clobbers the first).
  function setSide(i, side, name) {
    setPairs(prev => prev.map((p, idx) => (idx === i ? { ...p, [side]: name } : p)));
  }
  function removePair(i) {
    setPairs(prev => prev.filter((_, idx) => idx !== i));
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {pairs.map((pair, i) => {
        const lists = (
          <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
            <ColumnSelectList
              testId={i === 0 ? "column-list-left" : `column-list-left-${i}`}
              label={`Left · ${leftSource.source_name}`}
              columns={leftColumns}
              selectedName={pair.left}
              onSelect={name => setSide(i, "left", name)}
            />
            <span style={{ alignSelf: "center", fontSize: 16, color: "var(--text-3)", paddingTop: 14 }}>=</span>
            <ColumnSelectList
              testId={i === 0 ? "column-list-right" : `column-list-right-${i}`}
              label={`Right · ${rightSource.source_name}`}
              columns={rightColumns}
              selectedName={pair.right}
              onSelect={name => setSide(i, "right", name)}
              loading={rightLoading}
            />
          </div>
        );
        if (!composite) return <div key={i}>{lists}</div>;
        return (
          <div key={i} style={{
            border: "1px solid var(--border)", borderRadius: "var(--radius)",
            background: "var(--panel-2)", padding: 10,
          }}>
            <div style={{ display: "flex", alignItems: "center", marginBottom: 6 }}>
              <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-3)", flex: 1 }}>key {i + 1}</span>
              <button
                onClick={() => removePair(i)}
                aria-label={`Remove key ${i + 1}`}
                style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-4)", display: "inline-flex", padding: 2 }}
              >
                <Icon name="close" size={13} />
              </button>
            </div>
            {lists}
          </div>
        );
      })}
      <button
        onClick={() => setPairs(prev => [...prev, { left: null, right: null }])}
        style={{
          alignSelf: "flex-start", display: "inline-flex", alignItems: "center", gap: 5,
          padding: "5px 10px", fontSize: 12, cursor: "pointer",
          background: "transparent", color: "var(--accent)",
          border: "1px dashed var(--accent-line)", borderRadius: "var(--radius)",
        }}
      >
        <Icon name="plus" size={12} /> Add key
      </button>
    </div>
  );
}

function JoinTypeSelect({ value, onChange }) {
  const hint = (JOIN_TYPE_OPTIONS.find(o => o.value === value) || JOIN_TYPE_OPTIONS[0]).hint;
  return (
    <div>
      <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-3)", marginBottom: 5 }}>Join type</div>
      <div style={{ display: "flex", border: "1px solid var(--border)", borderRadius: "var(--radius)", overflow: "hidden" }}>
        {JOIN_TYPE_OPTIONS.map(o => {
          const active = value === o.value;
          return (
            <button
              key={o.value}
              onClick={() => onChange(o.value)}
              style={{
                flex: 1, padding: "5px 0", fontSize: 12, cursor: "pointer", border: "none",
                borderRight: "1px solid var(--border)",
                background: active ? "var(--accent)" : "transparent",
                color: active ? "var(--accent-ink)" : "var(--text-2)",
                fontWeight: active ? 600 : 500,
              }}
            >
              {o.value}
            </button>
          );
        })}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-4)", marginTop: 5 }}>{hint}</div>
    </div>
  );
}

function JoinModal({ open, onClose, currentSource, sources, fetchRightColumns, onSubmit, initialConfig }) {
  const { Modal, Btn, Icon } = window.__UI__;
  const [step, setStep] = useState(1);
  const [selectedSourceId, setSelectedSourceId] = useState(null);
  const [transformedBySource, setTransformedBySource] = useState({});
  const [pairs, setPairs] = useState([{ left: null, right: null }]);
  // #214: the right source the current `pairs` were built against. goNext only
  // resets pairs when the source actually changed, so a Back -> Next round-trip
  // (or an edit pre-fill) does not silently wipe configured key pairs.
  const [pairsForSource, setPairsForSource] = useState(null);
  const [joinType, setJoinType] = useState("inner");
  const [rightColumns, setRightColumns] = useState([]);
  const [rightLoading, setRightLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  // Edit mode (#209): when an initialConfig is provided the modal is editing an
  // existing placed join step rather than creating a new one. It seeds from the
  // saved config, opens directly on step 2, and the SidePanel routes onSubmit to
  // a PATCH instead of a POST.
  const isEdit = !!initialConfig;

  // Reset all modal state each time it opens. In edit mode, seed from the saved
  // builtin_config and jump straight to the key-pair step.
  useEffect(() => {
    if (!open) return;
    if (initialConfig) {
      const rid = initialConfig.right_source_id || null;
      const ut = !!initialConfig.use_transformed;
      setSelectedSourceId(rid);
      setTransformedBySource(rid ? { [rid]: ut } : {});
      setPairs(
        Array.isArray(initialConfig.on) && initialConfig.on.length > 0
          ? initialConfig.on.map(c => ({ left: c.left_col, right: c.right_col }))
          : [{ left: null, right: null }]
      );
      setPairsForSource(rid);  // #214: pairs are tied to the seeded source
      setJoinType(initialConfig.join_type || "inner");
      setRightColumns([]);
      setRightLoading(false);
      setSubmitting(false);
      setSubmitError(null);
      setStep(2);
      if (rid) {
        setRightLoading(true);
        Promise.resolve(fetchRightColumns(rid, ut))
          .then(cols => { setRightColumns(Array.isArray(cols) ? cols : []); setRightLoading(false); })
          .catch(() => { setRightColumns([]); setRightLoading(false); });
      }
      return;
    }
    setStep(1);
    setSelectedSourceId(null);
    setTransformedBySource({});
    setPairs([{ left: null, right: null }]);
    setPairsForSource(null);  // #214: fresh create flow — next goNext resets pairs
    setJoinType("inner");
    setRightColumns([]);
    setRightLoading(false);
    setSubmitting(false);
    setSubmitError(null);
  }, [open, initialConfig]);

  const candidates = useMemo(
    () => (sources || []).filter(s => s.source_id !== (currentSource && currentSource.source_id)),
    [sources, currentSource]
  );
  const selectedSource = candidates.find(s => s.source_id === selectedSourceId) || null;
  const useTransformed = !!transformedBySource[selectedSourceId];
  const leftColumns = (currentSource && currentSource.columns) || [];

  function loadRightColumns(sourceId, ut) {
    setRightLoading(true);
    Promise.resolve(fetchRightColumns(sourceId, ut))
      .then(cols => { setRightColumns(Array.isArray(cols) ? cols : []); setRightLoading(false); })
      .catch(() => { setRightColumns([]); setRightLoading(false); });
  }

  function goNext() {
    if (!selectedSourceId) return;
    // Only reset key pairs when the right source changed since they were built
    // (#214) — otherwise preserve them across a Back -> Next round-trip.
    if (selectedSourceId !== pairsForSource) {
      setPairs([{ left: null, right: null }]);
      setPairsForSource(selectedSourceId);
    }
    setStep(2);
    loadRightColumns(selectedSourceId, !!transformedBySource[selectedSourceId]);
  }

  const allPaired = pairs.length > 0 && pairs.every(p => p.left && p.right);

  function submit() {
    if (!allPaired || submitting) return;
    const config = {
      right_source_id: selectedSourceId,
      use_transformed: !!transformedBySource[selectedSourceId],
      join_type: joinType,
      on: pairs.map(p => ({ left_col: p.left, right_col: p.right })),
      keep_columns: "all",
    };
    setSubmitting(true);
    setSubmitError(null);
    Promise.resolve(onSubmit(config))
      .then(res => {
        setSubmitting(false);
        if (res && res.ok === false) { setSubmitError(res.detail || "Could not add join step."); return; }
        onClose && onClose();
      })
      .catch(e => { setSubmitting(false); setSubmitError(String(e && e.message ? e.message : e)); });
  }

  const footer = step === 1 ? (
    <>
      <Btn variant="ghost" onClick={onClose}>Cancel</Btn>
      <Btn variant="primary" onClick={goNext} disabled={!selectedSourceId || candidates.length === 0}>Next</Btn>
    </>
  ) : (
    <>
      <Btn variant="ghost" icon="back" onClick={() => setStep(1)}>Back</Btn>
      <Btn variant="ghost" onClick={onClose}>Cancel</Btn>
      <Btn variant="primary" onClick={submit} disabled={!allPaired || submitting}>Add step</Btn>
    </>
  );

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Add join"
      icon="join"
      width={460}
      headerExtra={<StepRail step={step} />}
      footer={footer}
    >
      {step === 1 && (
        candidates.length === 0 ? (
          <div style={{
            display: "flex", flexDirection: "column", alignItems: "center", gap: 10,
            padding: "30px 20px", textAlign: "center",
            border: "2px dashed var(--border)", borderRadius: "var(--radius)", color: "var(--text-4)",
          }}>
            <span style={{ color: "var(--text-3)" }}><Icon name="join" size={28} /></span>
            <div style={{ fontSize: 13 }}>No other reports available. Import a report on the Data screen first.</div>
          </div>
        ) : (
          <div>
            {candidates.map(s => (
              <SourceRow
                key={s.source_id}
                source={s}
                selected={selectedSourceId === s.source_id}
                onSelect={setSelectedSourceId}
                useTransformed={!!transformedBySource[s.source_id]}
                onToggleTransformed={(id, v) => setTransformedBySource(prev => ({ ...prev, [id]: v }))}
              />
            ))}
          </div>
        )
      )}

      {step === 2 && selectedSource && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ fontSize: 13, color: "var(--text-2)" }}>
            Joining <strong>{currentSource.source_name}</strong> with <strong>{selectedSource.source_name}</strong>
            {useTransformed && <span style={{ color: "var(--text-4)" }}> (transformed)</span>}
          </div>
          <KeyPairBuilder
            pairs={pairs}
            setPairs={setPairs}
            leftSource={currentSource}
            rightSource={selectedSource}
            leftColumns={leftColumns}
            rightColumns={rightColumns}
            rightLoading={rightLoading}
          />
          <JoinTypeSelect value={joinType} onChange={setJoinType} />
          {submitError && (
            <div style={{ fontSize: 12, color: "var(--bad)" }}>{submitError}</div>
          )}
        </div>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// FilterModal — configure a built-in filter step: keep rows where a column
// matches a predicate. Single step (column + condition + value); mirrors the
// JoinModal create/edit contract (open / onClose / onSubmit / initialConfig).
// Operators mirror the backend (_FILTER_COMPARISONS + contains/not_contains are
// binary; is_null/is_not_null take no value). #40 prep / filter end-to-end.
// ---------------------------------------------------------------------------

// Operator options: value + label + whether a comparison value is required.
const FILTER_OPERATORS = [
  { value: "eq", label: "= (equals)", needsValue: true },
  { value: "neq", label: "≠ (not equals)", needsValue: true },
  { value: "gt", label: "> (greater than)", needsValue: true },
  { value: "gte", label: "≥ (at least)", needsValue: true },
  { value: "lt", label: "< (less than)", needsValue: true },
  { value: "lte", label: "≤ (at most)", needsValue: true },
  { value: "contains", label: "contains", needsValue: true },
  { value: "not_contains", label: "does not contain", needsValue: true },
  { value: "is_null", label: "is null", needsValue: false },
  { value: "is_not_null", label: "is not null", needsValue: false },
];

function filterOperatorNeedsValue(op) {
  const found = FILTER_OPERATORS.find(o => o.value === op);
  return found ? found.needsValue : true;
}

const _filterFieldStyle = {
  fontSize: 13, padding: "5px 8px", borderRadius: "var(--radius)",
  border: "1px solid var(--border)", background: "var(--panel-2)",
  color: "var(--text)", width: "100%", boxSizing: "border-box",
};

function FilterModal({ open, onClose, currentSource, onSubmit, initialConfig }) {
  const { Modal, Btn } = window.__UI__;
  const [column, setColumn] = useState("");
  const [operator, setOperator] = useState("eq");
  const [value, setValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const isEdit = !!initialConfig;

  // Reset on open. Edit mode seeds from the saved config so the placed step
  // round-trips (Principle 7); a nullary operator (is_null) leaves value blank.
  useEffect(() => {
    if (!open) return;
    if (initialConfig) {
      setColumn(initialConfig.column || "");
      setOperator(initialConfig.operator || "eq");
      setValue(initialConfig.value != null ? String(initialConfig.value) : "");
    } else {
      setColumn("");
      setOperator("eq");
      setValue("");
    }
    setSubmitting(false);
    setSubmitError(null);
  }, [open, initialConfig]);

  const columns = (currentSource && currentSource.columns) || [];
  const needsValue = filterOperatorNeedsValue(operator);
  const canSubmit = !!column && !!operator && (!needsValue || value.trim() !== "");

  function submit() {
    if (!canSubmit || submitting) return;
    // Nullary operators (is_null / is_not_null) carry no value — match the backend
    // shape which rejects a value-less binary op and ignores value for nullary ops.
    const config = needsValue ? { column, operator, value } : { column, operator };
    setSubmitting(true);
    setSubmitError(null);
    Promise.resolve(onSubmit(config))
      .then(res => {
        setSubmitting(false);
        if (res && res.ok === false) { setSubmitError(res.detail || "Could not add filter step."); return; }
        onClose && onClose();
      })
      .catch(e => { setSubmitting(false); setSubmitError(String(e && e.message ? e.message : e)); });
  }

  const footer = (
    <>
      <Btn variant="ghost" onClick={onClose}>Cancel</Btn>
      <Btn variant="primary" onClick={submit} disabled={!canSubmit || submitting}>
        {isEdit ? "Save filter" : "Add step"}
      </Btn>
    </>
  );

  return (
    <Modal open={open} onClose={onClose} title={isEdit ? "Edit filter" : "Add filter"} width={420} footer={footer}>
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: 12, color: "var(--text-2)" }}>Column</span>
          <select
            data-testid="filter-column"
            value={column}
            onChange={e => setColumn(e.target.value)}
            style={{ ..._filterFieldStyle, cursor: "pointer" }}
          >
            <option value="">Select a column…</option>
            {columns.map(c => (
              <option key={c.column_id || c.column_name} value={c.column_name}>{c.column_name}</option>
            ))}
          </select>
        </label>

        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: 12, color: "var(--text-2)" }}>Condition</span>
          <select
            data-testid="filter-operator"
            value={operator}
            onChange={e => setOperator(e.target.value)}
            style={{ ..._filterFieldStyle, cursor: "pointer" }}
          >
            {FILTER_OPERATORS.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>

        {needsValue && (
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span style={{ fontSize: 12, color: "var(--text-2)" }}>Value</span>
            <input
              data-testid="filter-value"
              type="text"
              placeholder="Value to compare"
              value={value}
              onChange={e => setValue(e.target.value)}
              style={_filterFieldStyle}
            />
          </label>
        )}

        <div style={{ fontSize: 12, color: "var(--text-4)" }}>
          Keeps rows where <strong>{column || "the column"}</strong>{" "}
          {needsValue ? "matches the condition" : operator.replace("_", " ")}.
        </div>

        {submitError && (
          <div style={{ fontSize: 12, color: "var(--bad)" }}>{submitError}</div>
        )}
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// RenameModal — configure a built-in rename step: rename selected columns in the
// report output (#40). One or more column→new-name pairs. The "from" field is a
// datalist — pick a registered column OR type a join-added one (the free-text
// escape hatch). Edit mode restores saved pairs in order (Principle 7). The step is
// pinned last + one-per-report (enforced backend-side). Mirrors the Filter/Join
// create/edit contract (open / onClose / onSubmit / initialConfig).
// ---------------------------------------------------------------------------

function RenameModal({ open, onClose, currentSource, onSubmit, initialConfig }) {
  const { Modal, Btn } = window.__UI__;
  const [pairs, setPairs] = useState([{ from: "", to: "" }]);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const isEdit = !!initialConfig;

  useEffect(() => {
    if (!open) return;
    const saved = initialConfig && initialConfig.renames;
    if (saved && Object.keys(saved).length) {
      // Preserve saved order (JSON object insertion order) — Principle 7 round-trip.
      setPairs(Object.entries(saved).map(([from, to]) => ({ from, to: String(to) })));
    } else {
      setPairs([{ from: "", to: "" }]);
    }
    setSubmitting(false);
    setSubmitError(null);
  }, [open, initialConfig]);

  const columns = (currentSource && currentSource.columns) || [];
  const listId = "rename-cols";

  const complete = pairs.filter(p => p.from.trim() && p.to.trim());
  const fromNames = complete.map(p => p.from.trim());
  const toNames = complete.map(p => p.to.trim());
  const dupFrom = new Set(fromNames).size !== fromNames.length;
  const dupTo = new Set(toNames).size !== toNames.length;
  const canSubmit = complete.length > 0 && !dupFrom && !dupTo;

  function setPair(i, key, val) {
    setPairs(prev => prev.map((p, idx) => (idx === i ? { ...p, [key]: val } : p)));
  }
  function addPair() { setPairs(prev => [...prev, { from: "", to: "" }]); }
  function removePair(i) {
    setPairs(prev => (prev.length > 1 ? prev.filter((_, idx) => idx !== i) : prev));
  }

  function submit() {
    if (!canSubmit || submitting) return;
    const renames = {};
    complete.forEach(p => { renames[p.from.trim()] = p.to.trim(); });
    setSubmitting(true);
    setSubmitError(null);
    Promise.resolve(onSubmit({ renames }))
      .then(res => {
        setSubmitting(false);
        if (res && res.ok === false) { setSubmitError(res.detail || "Could not add rename step."); return; }
        onClose && onClose();
      })
      .catch(e => { setSubmitting(false); setSubmitError(String(e && e.message ? e.message : e)); });
  }

  const footer = (
    <>
      <Btn variant="ghost" onClick={onClose}>Cancel</Btn>
      <Btn variant="primary" onClick={submit} disabled={!canSubmit || submitting}>
        {isEdit ? "Save rename" : "Add step"}
      </Btn>
    </>
  );

  return (
    <Modal open={open} onClose={onClose} title={isEdit ? "Edit rename" : "Add rename"} width={460} footer={footer}>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ fontSize: 12, color: "var(--text-3)" }}>
          Rename columns in the report output. Runs last; one rename step per report.
        </div>
        <datalist id={listId}>
          {columns.map(c => <option key={c.column_id || c.column_name} value={c.column_name} />)}
        </datalist>
        {pairs.map((p, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input
              data-testid={"rename-from-" + i}
              list={listId}
              placeholder="Column"
              value={p.from}
              onChange={e => setPair(i, "from", e.target.value)}
              style={{ ..._filterFieldStyle, flex: 1 }}
            />
            <span style={{ color: "var(--text-4)", fontSize: 13 }}>→</span>
            <input
              data-testid={"rename-to-" + i}
              placeholder="New name"
              value={p.to}
              onChange={e => setPair(i, "to", e.target.value)}
              style={{ ..._filterFieldStyle, flex: 1 }}
            />
            <button
              type="button"
              data-testid={"rename-remove-" + i}
              onClick={() => removePair(i)}
              disabled={pairs.length <= 1}
              title="Remove"
              style={{
                background: "none", border: "none", color: "var(--text-4)", fontSize: 16,
                padding: "0 4px", cursor: pairs.length > 1 ? "pointer" : "default",
                opacity: pairs.length > 1 ? 1 : 0.3,
              }}
            >×</button>
          </div>
        ))}
        <div>
          <Btn variant="ghost" onClick={addPair}>+ Add another</Btn>
        </div>
        {dupFrom && <div style={{ fontSize: 12, color: "var(--bad)" }}>A column is renamed more than once.</div>}
        {dupTo && <div style={{ fontSize: 12, color: "var(--bad)" }}>Two columns map to the same new name.</div>}
        {submitError && <div style={{ fontSize: 12, color: "var(--bad)" }}>{submitError}</div>}
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Side panel
// ---------------------------------------------------------------------------

function SidePanel({ source, onClose, onNavigate, flash }) {
  const { LoadingState, InlineError } = window.__UI__;
  const [pipeline, setPipeline] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [resultTags, setResultTags] = useState({});
  // Pending step card state (used for both new attach and edit)
  // editStepId: when set, save calls PATCH instead of POST
  const [pendingStep, setPendingStep] = useState(null); // null | { dryRunResult, attachBody, stepName, editStepId? }
  const [pendingDryRunning, setPendingDryRunning] = useState(false);
  const [pendingSaving, setPendingSaving] = useState(false);
  const [pendingSaveError, setPendingSaveError] = useState(null);
  // Join built-in modal
  const [joinModalOpen, setJoinModalOpen] = useState(false);
  const [joinSources, setJoinSources] = useState([]);
  // Filter built-in modal (open from a filter-card drop or a placed filter edit)
  const [filterModalOpen, setFilterModalOpen] = useState(false);
  // Rename built-in modal (open from a rename-card drop or a placed rename edit)
  const [renameModalOpen, setRenameModalOpen] = useState(false);
  // Edit a placed built-in step (#209): holds the step being edited, or null. When
  // set, the join modal opens seeded from its builtin_config and saves via PATCH.
  const [editingBuiltin, setEditingBuiltin] = useState(null);
  // All sources, loaded once for the canvas so a built-in card can resolve its
  // right_source_id → source_name in the config summary.
  const [allSources, setAllSources] = useState([]);

  // Load all sources (with columns + row counts) when the join modal opens, then
  // best-effort enrich each candidate with its pipeline step count (the /sources
  // list does not carry one — drives the per-source "use transformed" toggle).
  useEffect(() => {
    if (!joinModalOpen) return;
    let cancelled = false;
    fetch("/sources")
      .then(r => r.ok ? r.json() : [])
      .then(list => {
        const sources = Array.isArray(list) ? list : [];
        if (!cancelled) setJoinSources(sources);
        sources
          .filter(s => s.source_id !== source.source_id)
          .forEach(s => {
            fetch("/sources/" + s.source_id + "/pipeline")
              .then(r => r.ok ? r.json() : null)
              .then(p => {
                const steps = p && Array.isArray(p.steps) ? p.steps.length : 0;
                if (!cancelled && steps > 0) {
                  setJoinSources(prev => prev.map(x => x.source_id === s.source_id ? { ...x, steps } : x));
                }
              })
              .catch(() => {});
          });
      })
      .catch(() => { if (!cancelled) setJoinSources([]); });
    return () => { cancelled = true; };
  }, [joinModalOpen, source && source.source_id]);

  // Fetch a right-hand source's columns for step 2. Honors the use-transformed
  // toggle: when on, requests the source's transformed column set from the
  // join-columns endpoint (which resolves through resolve_frame); when off, the
  // raw registered columns. (runner-resolution-model slice 2 / #18)
  function fetchJoinRightColumns(rightSourceId, useTransformed) {
    const url = "/sources/" + rightSourceId + "/join-columns?transformed=" + (useTransformed ? "true" : "false");
    return fetch(url)
      .then(r => r.ok ? r.json() : { columns: [] })
      .then(d => (d && Array.isArray(d.columns)) ? d.columns : []);
  }

  // Load all sources once so the canvas built-in cards can resolve right-source
  // names for their config summary (#209).
  useEffect(() => {
    let cancelled = false;
    fetch("/sources")
      .then(r => r.ok ? r.json() : [])
      .then(list => { if (!cancelled) setAllSources(Array.isArray(list) ? list : []); })
      .catch(() => { if (!cancelled) setAllSources([]); });
    return () => { cancelled = true; };
  }, []);

  function submitJoinStep(builtinConfig) {
    // Edit mode (#209): PATCH the existing step instead of POSTing a new one. On
    // success loadPipeline() re-fetches so the edited card updates without reload.
    if (editingBuiltin) {
      return fetch("/sources/" + source.source_id + "/attach-builtin/" + editingBuiltin.step_id, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ builtin_config: builtinConfig }),
      })
        .then(r => r.json().then(data => ({ ok: r.ok && data.ok !== false, ...data })))
        .then(res => {
          if (res.ok) { loadPipeline(); flash && flash("Join step updated.", "ok"); }
          return res;
        });
    }
    return fetch("/sources/" + source.source_id + "/attach-builtin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ builtin_type: "join", builtin_config: builtinConfig }),
    })
      .then(r => r.json().then(data => ({ ok: r.ok && data.ok !== false, ...data })))
      .then(res => {
        if (res.ok) { loadPipeline(); flash && flash("Join step added.", "ok"); }
        return res;
      });
  }

  // Attach (POST) or edit (PATCH) a filter built-in step. Mirrors submitJoinStep
  // with builtin_type="filter"; on success re-fetches the pipeline so the card updates.
  function submitFilterStep(builtinConfig) {
    if (editingBuiltin) {
      return fetch("/sources/" + source.source_id + "/attach-builtin/" + editingBuiltin.step_id, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ builtin_config: builtinConfig }),
      })
        .then(r => r.json().then(data => ({ ok: r.ok && data.ok !== false, ...data })))
        .then(res => {
          if (res.ok) { loadPipeline(); flash && flash("Filter step updated.", "ok"); }
          return res;
        });
    }
    return fetch("/sources/" + source.source_id + "/attach-builtin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ builtin_type: "filter", builtin_config: builtinConfig }),
    })
      .then(r => r.json().then(data => ({ ok: r.ok && data.ok !== false, ...data })))
      .then(res => {
        if (res.ok) { loadPipeline(); flash && flash("Filter step added.", "ok"); }
        return res;
      });
  }

  // Attach (POST) or edit (PATCH) a rename built-in step. Mirrors submitFilterStep
  // with builtin_type="rename"; the backend pins it last + enforces one-per-source.
  function submitRenameStep(builtinConfig) {
    if (editingBuiltin) {
      return fetch("/sources/" + source.source_id + "/attach-builtin/" + editingBuiltin.step_id, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ builtin_config: builtinConfig }),
      })
        .then(r => r.json().then(data => ({ ok: r.ok && data.ok !== false, ...data })))
        .then(res => {
          if (res.ok) { loadPipeline(); flash && flash("Rename step updated.", "ok"); }
          return res;
        });
    }
    return fetch("/sources/" + source.source_id + "/attach-builtin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ builtin_type: "rename", builtin_config: builtinConfig }),
    })
      .then(r => r.json().then(data => ({ ok: r.ok && data.ok !== false, ...data })))
      .then(res => {
        if (res.ok) { loadPipeline(); flash && flash("Rename step added.", "ok"); }
        return res;
      });
  }

  // Open the right modal seeded from a placed built-in step's config (#209 edit).
  // Routes by builtin_type: join -> JoinModal, filter -> FilterModal, rename ->
  // RenameModal (others: no-op).
  function handleEditBuiltin(step) {
    if (!step) return;
    if (step.builtin_type === "join") {
      setEditingBuiltin(step);
      setJoinModalOpen(true);
    } else if (step.builtin_type === "filter") {
      setEditingBuiltin(step);
      setFilterModalOpen(true);
    } else if (step.builtin_type === "rename") {
      setEditingBuiltin(step);
      setRenameModalOpen(true);
    }
  }

  function handleCloseJoinModal() {
    setJoinModalOpen(false);
    setEditingBuiltin(null);
  }

  function handleCloseFilterModal() {
    setFilterModalOpen(false);
    setEditingBuiltin(null);
  }

  function handleCloseRenameModal() {
    setRenameModalOpen(false);
    setEditingBuiltin(null);
  }

  function loadPipeline() {
    if (!source) return;
    setLoading(true);
    setError(null);
    fetch("/pipelines/" + source.source_id)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(data => { setPipeline(data); setLoading(false); })
      .catch(err => { setError("Failed to load pipeline (" + err + ")."); setLoading(false); });
  }

  useEffect(() => {
    setPipeline(null);
    setResultTags({});
    loadPipeline();
  }, [source && source.source_id]);

  function handleDragOver(e) {
    if (e.dataTransfer.types.some(t => t.startsWith("palette/"))) {
      e.preventDefault();
      setIsDragOver(true);
    }
  }

  function handleDragLeave() {
    setIsDragOver(false);
  }

  function handleDrop(e) {
    e.preventDefault();
    setIsDragOver(false);
    const functionId = e.dataTransfer.getData("palette/function_id");
    const setId = e.dataTransfer.getData("palette/set_id");
    const builtinType = e.dataTransfer.getData("palette/builtin_type");

    // Builtin drop — join + filter + rename open their configuration modals;
    // remaining built-ins (e.g. pivot) are not wired yet.
    if (builtinType) {
      if (builtinType === "join") {
        setJoinModalOpen(true);
      } else if (builtinType === "filter") {
        setFilterModalOpen(true);
      } else if (builtinType === "rename") {
        setRenameModalOpen(true);
      } else {
        const label = builtinType.charAt(0).toUpperCase() + builtinType.slice(1);
        flash && flash(`${label} modal coming soon`, "ok");
      }
      return;
    }

    const draggedName = functionId
      ? e.dataTransfer.getData("palette/function_name")
      : e.dataTransfer.getData("palette/set_name");
    if (!functionId && !setId) return;

    // Cancel any existing pending card and start a new dry-run
    setPendingStep(null);
    setPendingSaveError(null);
    setPendingDryRunning(true);

    const attachBody = functionId
      ? { function_id: functionId, bindings: [] }
      : { set_id: setId, bindings: [] };
    const dryRunBody = functionId ? { function_id: functionId } : { set_id: setId };

    fetch("/pipelines/" + source.source_id + "/steps?dry_run=true", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(dryRunBody),
    })
      .then(r => r.json())
      .then(dryRunResult => {
        setPendingDryRunning(false);
        setPendingStep({ dryRunResult, attachBody, stepName: draggedName });
      })
      .catch(() => {
        setPendingDryRunning(false);
        setPendingStep({ dryRunResult: { params: [] }, attachBody, stepName: draggedName });
      });
  }

  function handleEditStep(step) {
    // Find the set_id/function_id for the dry-run
    const setId = step.set_id;
    setPendingStep(null);
    setPendingSaveError(null);
    setPendingDryRunning(true);

    fetch("/pipelines/" + source.source_id + "/steps?dry_run=true", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ set_id: setId }),
    })
      .then(r => r.json())
      .then(dryRunResult => {
        setPendingDryRunning(false);
        setPendingStep({
          dryRunResult,
          attachBody: null,
          stepName: step.set_name,
          editStepId: step.source_function_map_id,
        });
      })
      .catch(() => {
        setPendingDryRunning(false);
        flash && flash("Could not load step parameters", "error");
      });
  }

  function handlePendingSave(bindings, scalarValues, extras) {
    if (!pendingStep) return;
    extras = extras || {};
    setPendingSaving(true);
    setPendingSaveError(null);

    const isEdit = !!pendingStep.editStepId;

    if (isEdit) {
      // Build bindings as dict: param_id -> [column_id, ...]
      const bindingsDict = {};
      bindings.forEach(b => { bindingsDict[b.param_id] = b.column_ids; });
      const patchBody = { bindings: bindingsDict };
      if (Object.keys(scalarValues).length > 0) patchBody.scalar_values = scalarValues;

      fetch("/pipelines/" + source.source_id + "/steps/" + pendingStep.editStepId, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patchBody),
      })
        .then(r => r.json().then(data => ({ ok: r.ok, data })))
        .then(({ ok, data }) => {
          setPendingSaving(false);
          if (ok && data.ok) {
            setPendingStep(null);
            setPendingSaveError(null);
            loadPipeline();
          } else {
            setPendingSaveError(data.detail || "Edit failed");
          }
        })
        .catch(() => {
          setPendingSaving(false);
          setPendingSaveError("Edit failed — check the server log");
        });
      return;
    }

    // New attach: POST. Scalar values (and str plain-string literals) are sent in
    // the POST body so the backend can apply the str binding-exemption and persist
    // them in one request — no follow-up PATCH needed (Bug #186).
    const body = { ...pendingStep.attachBody, bindings };
    if (Object.keys(scalarValues).length > 0) {
      body.scalar_values = scalarValues;
    }
    // Slice 4: forward output_mode + replace targets / append name chosen in the modal.
    if (extras.output_mode) body.output_mode = extras.output_mode;
    if (extras.output_targets) body.output_targets = extras.output_targets;
    if (extras.append_name) body.append_name = extras.append_name;
    fetch("/pipelines/" + source.source_id + "/steps", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(r => r.json().then(data => ({ ok: r.ok, data })))
      .then(({ ok, data }) => {
        setPendingSaving(false);
        if (ok && data.ok) {
          setPendingStep(null);
          setPendingSaveError(null);
          loadPipeline();
        } else {
          const msg = data.detail || (data.missing_params ? "Missing bindings: " + data.missing_params.join(", ") : "Attach failed");
          setPendingSaveError(msg);
        }
      })
      .catch(() => {
        setPendingSaving(false);
        setPendingSaveError("Attach failed — check the server log");
      });
  }

  function handlePendingCancel() {
    setPendingStep(null);
    setPendingSaveError(null);
    setPendingDryRunning(false);
  }

  return (
    <div style={{
      width: 360, flexShrink: 0,
      borderLeft: "1px solid var(--border)",
      display: "flex", flexDirection: "column",
      overflow: "hidden",
    }}>
      <div style={{
        padding: "12px 16px",
        borderBottom: "1px solid var(--border)",
        display: "flex", alignItems: "center", gap: 10, flexShrink: 0,
      }}>
        <span style={{ fontWeight: 600, fontSize: 14, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {source.source_name}
        </span>
        <button
          onClick={onClose}
          title="Close panel"
          style={{
            background: "none", border: "none", cursor: "pointer",
            color: "var(--text-4)", fontSize: 18, lineHeight: 1,
            padding: "2px 4px", borderRadius: "var(--radius)",
          }}
        >
          x
        </button>
      </div>

      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        style={{
          flex: 1, overflow: "auto", padding: 14,
          outline: isDragOver ? "2px dashed var(--accent)" : "none",
          background: isDragOver ? "var(--accent-soft)" : "transparent",
          transition: "background .1s",
        }}
      >
        {pendingDryRunning && <LoadingState label="Loading parameters…" />}
        {loading && <LoadingState />}
        {error && <InlineError variant="panel">{error}</InlineError>}
        {!loading && !error && pipeline && (
          <PipelineCanvas
            sourceId={source.source_id}
            steps={pipeline.steps}
            sources={allSources}
            onReloadPipeline={loadPipeline}
            resultTags={resultTags}
            onNavigateResults={() => onNavigate && onNavigate("results", {})}
            onEditStep={handleEditStep}
            onEditBuiltin={handleEditBuiltin}
          />
        )}
        {!loading && !error && pipeline && pipeline.steps.length === 0 && !pendingStep && (
          <div style={{
            marginTop: 10, padding: "14px 10px", borderRadius: "var(--radius)",
            border: "2px dashed var(--border)", color: "var(--text-4)",
            fontSize: 12, textAlign: "center",
          }}>
            Drag a function or set here to add a pipeline step.
          </div>
        )}
        {pendingStep && (
          <PendingStepCard
            dryRunResult={pendingStep.dryRunResult}
            stepName={pendingStep.stepName}
            onSave={handlePendingSave}
            onCancel={handlePendingCancel}
            saving={pendingSaving}
            saveError={pendingSaveError}
          />
        )}
      </div>

      <JoinModal
        open={joinModalOpen}
        onClose={handleCloseJoinModal}
        currentSource={joinSources.find(s => s.source_id === source.source_id) || source}
        sources={joinSources}
        fetchRightColumns={fetchJoinRightColumns}
        onSubmit={submitJoinStep}
        initialConfig={editingBuiltin && editingBuiltin.builtin_type === "join" ? editingBuiltin.builtin_config : null}
      />

      <FilterModal
        open={filterModalOpen}
        onClose={handleCloseFilterModal}
        currentSource={(pipeline && pipeline.source) || source}
        onSubmit={submitFilterStep}
        initialConfig={editingBuiltin && editingBuiltin.builtin_type === "filter" ? editingBuiltin.builtin_config : null}
      />

      <RenameModal
        open={renameModalOpen}
        onClose={handleCloseRenameModal}
        currentSource={(pipeline && pipeline.source) || source}
        onSubmit={submitRenameStep}
        initialConfig={editingBuiltin && editingBuiltin.builtin_type === "rename" ? editingBuiltin.builtin_config : null}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main screen
// ---------------------------------------------------------------------------

function ScreenBuilder({ flash, onNavigate }) {
  const { SourceBadge } = window.__UI__;
  const [sources, setSources] = useState([]);
  const [selectedSource, setSelectedSource] = useState(null);

  useEffect(() => {
    fetch("/sources")
      .then(r => r.ok ? r.json() : [])
      .then(setSources)
      .catch(() => {});
  }, []);

  function handleSelectSource(s) {
    if (selectedSource && selectedSource.source_id === s.source_id) {
      setSelectedSource(null);
    } else {
      setSelectedSource(s);
    }
  }

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div style={{
        padding: "16px 24px", borderBottom: "1px solid var(--border)", flexShrink: 0,
      }}>
        <div style={{ fontWeight: 600, fontSize: 16 }}>Report Builder</div>
        <div style={{ color: "var(--text-3)", fontSize: 12 }}>
          Select a report to view and edit its pipeline
        </div>
      </div>

      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>

        {/* Center area — report list */}
        <div style={{ flex: 1, overflow: "auto", padding: 24 }}>
          <div style={{
            fontSize: 11, fontWeight: 600, color: "var(--text-3)",
            letterSpacing: ".05em", textTransform: "uppercase",
            marginBottom: 12,
          }}>
            Reports
          </div>
          {sources.length === 0 ? (
            <div style={{ fontSize: 13, color: "var(--text-4)" }}>
              No reports registered. Import data from the Data screen first.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {sources.map(s => {
                const isSelected = selectedSource && selectedSource.source_id === s.source_id;
                return (
                  <div
                    key={s.source_id}
                    onClick={() => handleSelectSource(s)}
                    style={{
                      padding: "12px 16px",
                      background: isSelected ? "var(--accent-soft)" : "var(--panel)",
                      border: "1px solid " + (isSelected ? "var(--accent)" : "var(--border)"),
                      borderRadius: "var(--radius-lg)",
                      cursor: "pointer",
                      display: "flex", alignItems: "center", gap: 10,
                      transition: "background .1s, border-color .1s",
                    }}
                  >
                    <SourceBadge name={s.source_name} />
                    <div style={{ flex: 1 }}>
                      <div style={{
                        fontSize: 14, fontWeight: isSelected ? 600 : 500,
                        color: isSelected ? "var(--accent)" : "var(--text)",
                      }}>
                        {s.source_name}
                      </div>
                      {s.ingestion_method && (
                        <div style={{ fontSize: 11, color: "var(--text-4)", marginTop: 2 }}>
                          {s.ingestion_method}
                        </div>
                      )}
                    </div>
                    <span style={{ fontSize: 12, color: "var(--text-4)" }}>
                      {isSelected ? "v" : ">"}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Side panel — opens when a report is selected */}
        {selectedSource && (
          <SidePanel
            source={selectedSource}
            onClose={() => setSelectedSource(null)}
            onNavigate={onNavigate}
            flash={flash}
          />
        )}

        {/* Right panel — function / set / built-ins palette */}
        <RightPalette selectedSource={selectedSource} flash={flash} />

      </div>
    </div>
  );
}

window.__ScreenBuilder__ = ScreenBuilder;

// Named exports for the dev-time vitest harness only. In the browser the file is
// loaded as a Babel "module" (<script type="text/babel" data-type="module">), so
// these export statements are valid there too; the app itself consumes the
// components via the window.__ScreenBuilder__ global above.
export { ScreenBuilder, PendingStepCard, StepCard, ParamRow, FunctionRow, SidePanel, JoinModal, FilterModal, RenameModal, PaletteBuiltinCard, PaletteBuiltinDrawer, BuiltinStepCard, PipelineCanvas };
