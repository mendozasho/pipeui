// Report Builder screen — Phase E2: pipeline canvas with drag-to-reorder + run controls
const { useState, useEffect, useRef } = React;

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

function PendingStepCard({ dryRunResult, stepName, sourceColumns, onSave, onCancel, saving, saveError }) {
  const params = dryRunResult.params || [];

  // str params can be toggled between "text" (scalar) and "column" (column-backed) mode
  const [strModes, setStrModes] = React.useState(() => {
    const m = {};
    params.forEach(p => { if (p.param_type === "str") m[p.param_id] = "text"; });
    return m;
  });

  function initSelections() {
    const sel = {};
    params.forEach(p => {
      if (p.param_type === "pd.Series" || p.param_type === "column_backed") {
        sel[p.param_id] = (p.suggested_columns || []).map(c => c.column_id);
      }
    });
    return sel;
  }
  const [selections, setSelections] = React.useState(initSelections);
  const [scalarValues, setScalarValues] = React.useState({});

  // A str param in "column" mode is treated as required (must have ≥1 column selected)
  const requiredParams = params.filter(p =>
    p.param_type === "pd.Series" || p.param_type === "column_backed" ||
    (p.param_type === "str" && strModes[p.param_id] === "column")
  );
  const allRequiredFilled = requiredParams.every(p => (selections[p.param_id] || []).length > 0);

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

  function handleSave() {
    const bindings = params
      .filter(p =>
        p.param_type === "pd.Series" || p.param_type === "column_backed" ||
        (p.param_type === "str" && strModes[p.param_id] === "column")
      )
      .map(p => ({ param_id: p.param_id, column_ids: selections[p.param_id] || [] }))
      .filter(b => b.column_ids.length > 0);
    onSave(bindings, scalarValues);
  }

  const displayName = stepName || "Step";

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

      {params.map(p => {
        const isDataFrame = p.param_type === "pd.DataFrame";
        const isMultiCol = p.param_type === "pd.Series" || p.param_type === "column_backed";
        const isStr = p.param_type === "str";
        const strMode = strModes[p.param_id] || "text";
        const isScalar = !isDataFrame && !isMultiCol && !(isStr && strMode === "column");

        return (
          <div key={p.param_id} style={{
            padding: "8px 0",
            borderBottom: "1px solid var(--border-soft)",
            display: "flex", flexDirection: "column", gap: 5,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
              {p.function_name && (
                <span style={{ fontSize: 10, color: "var(--text-4)", fontFamily: "'Geist Mono', monospace" }}>
                  {p.function_name}
                </span>
              )}
              {p.function_name && <span style={{ fontSize: 10, color: "var(--text-4)" }}>/</span>}
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

            {isScalar && (
              <input
                type="text"
                placeholder="Python default"
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
              <div style={{ fontSize: 10, color: "var(--text-3)", marginBottom: 3 }}>
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
                {sourceColumns.length === 0 && (
                  <span style={{ fontSize: 10, color: "var(--text-4)", fontStyle: "italic" }}>No columns available.</span>
                )}
                {sourceColumns.map(col => {
                  const selected = (selections[p.param_id] || []).includes(col.column_id);
                  return (
                    <label key={col.column_id} style={{
                      display: "flex", alignItems: "center", gap: 5,
                      cursor: "pointer", fontSize: 11,
                      color: selected ? "var(--accent)" : "var(--text)",
                      fontWeight: selected ? 600 : 400,
                    }}>
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={() => handleColumnToggle(p.param_id, col.column_id)}
                        style={{ cursor: "pointer" }}
                      />
                      <span style={{ fontFamily: "'Geist Mono', monospace" }}>{col.column_name}</span>
                      <span style={{ fontSize: 10, color: "var(--text-4)" }}>{col.column_type}</span>
                    </label>
                  );
                })}
              </div>
              </>
            )}
          </div>
        );
      })}

      {saveError && (
        <div style={{ color: "#e05252", fontSize: 11, padding: "4px 0" }}>{saveError}</div>
      )}

      <div style={{ display: "flex", gap: 8, paddingTop: 2 }}>
        <button
          onClick={handleSave}
          disabled={!allRequiredFilled || saving}
          style={{
            flex: 1, padding: "7px 0", fontSize: 12, fontWeight: 600,
            borderRadius: "var(--radius)",
            border: "none",
            background: allRequiredFilled && !saving ? "var(--accent)" : "var(--panel-3)",
            color: allRequiredFilled && !saving ? "#fff" : "var(--text-4)",
            cursor: allRequiredFilled && !saving ? "pointer" : "not-allowed",
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

function StepCard({ step, sourceId, onRemoved, isDragging, onDragStart, onDragEnd, onDragOver, resultTag, runningSetId, onRunSet, onNavigateResults }) {
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
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragOver={onDragOver}
      style={{
        background: "var(--panel)", border: "1px solid var(--border)",
        borderRadius: "var(--radius-lg)", padding: "12px 14px",
        display: "flex", flexDirection: "column", gap: 8,
        opacity: isDragging ? 0.4 : 1,
        cursor: "grab",
        transition: "opacity .15s",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{
          fontSize: 10, fontWeight: 700,
          background: "var(--panel-3)", color: "var(--text-3)",
          borderRadius: 99, padding: "2px 7px", flexShrink: 0,
        }}>
          {"#" + step.position}
        </span>
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
          onClick={e => { e.stopPropagation(); if (onRunSet) onRunSet(step.source_function_map_id); }}
          disabled={runningSetId === step.source_function_map_id}
          title="Run this set"
          style={{
            background: "none", border: "none",
            cursor: runningSetId === step.source_function_map_id ? "default" : "pointer",
            color: runningSetId === step.source_function_map_id ? "var(--accent)" : "var(--text-4)",
            fontSize: 13, lineHeight: 1, padding: "2px 4px",
            borderRadius: "var(--radius)", flexShrink: 0,
            opacity: runningSetId === step.source_function_map_id ? 0.6 : 1,
          }}
          onMouseEnter={e => { if (runningSetId !== step.source_function_map_id) e.currentTarget.style.color = "var(--accent)"; }}
          onMouseLeave={e => { if (runningSetId !== step.source_function_map_id) e.currentTarget.style.color = "var(--text-4)"; }}
        >
          {runningSetId === step.source_function_map_id ? "..." : "▶"}
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
// Pipeline canvas — drag-to-reorder
// ---------------------------------------------------------------------------

function PipelineCanvas({ sourceId, steps, onReloadPipeline, resultTags, runningSetId, onRunSet, onNavigateResults }) {
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
      {localSteps.map((step, index) => (
        <StepCard
          key={step.source_function_map_id}
          step={step}
          sourceId={sourceId}
          onRemoved={onReloadPipeline}
          isDragging={dragIndexRef.current === index}
          onDragStart={() => handleDragStart(index)}
          onDragEnd={handleDragEnd}
          onDragOver={e => handleDragOver(e, index)}
          resultTag={resultTags && resultTags[step.source_function_map_id]}
          runningSetId={runningSetId}
          onRunSet={onRunSet}
          onNavigateResults={onNavigateResults}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Right palette — Functions + Sets tabs
// ---------------------------------------------------------------------------

function PaletteFunctionCard({ fn, onDragStart }) {
  return (
    <div
      draggable
      onDragStart={e => {
        e.dataTransfer.setData("palette/function_id", fn.function_id);
        e.dataTransfer.setData("palette/function_name", fn.function_name);
        if (onDragStart) onDragStart();
      }}
      title={fn.function_doc || fn.function_name}
      style={{
        padding: "7px 10px",
        borderRadius: "var(--radius)",
        border: "1px solid var(--border)",
        background: "var(--panel)",
        cursor: "grab",
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

function PaletteSetCard({ set, onDragStart }) {
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
      title={set.set_description || set.set_name}
      style={{
        padding: "7px 10px",
        borderRadius: "var(--radius)",
        border: "1px solid var(--border)",
        background: "var(--panel)",
        cursor: "grab",
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

function RightPalette({ selectedSource }) {
  const [activeTab, setActiveTab] = useState("functions");
  const [functions, setFunctions] = useState([]);
  const [sets, setSets] = useState([]);
  const [setsDetail, setSetsDetail] = useState({});
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      fetch("/functions").then(r => r.ok ? r.json() : []),
      fetch("/function-sets").then(r => r.ok ? r.json() : []),
    ]).then(([fns, sts]) => {
      setFunctions(Array.isArray(fns) ? fns : []);
      setSets(Array.isArray(sts) ? sts : []);
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
      </div>
      <div style={{ flex: 1, overflow: "auto", padding: "10px 10px 10px" }}>
        {loading && <div style={{ fontSize: 11, color: "var(--text-4)", textAlign: "center", paddingTop: 20 }}>Loading...</div>}
        {!loading && activeTab === "functions" && (
          <>
            {validationFns.length > 0 && (
              <>
                <div style={{ fontSize: 10, fontWeight: 700, color: "var(--text-3)", letterSpacing: ".06em", textTransform: "uppercase", marginBottom: 6 }}>Validation</div>
                {validationFns.map(fn => <PaletteFunctionCard key={fn.function_id} fn={fn} />)}
              </>
            )}
            {transformFns.length > 0 && (
              <>
                <div style={{ fontSize: 10, fontWeight: 700, color: "var(--text-3)", letterSpacing: ".06em", textTransform: "uppercase", marginBottom: 6, marginTop: validationFns.length > 0 ? 10 : 0 }}>Transform</div>
                {transformFns.map(fn => <PaletteFunctionCard key={fn.function_id} fn={fn} />)}
              </>
            )}
            {functions.length === 0 && (
              <div style={{ fontSize: 11, color: "var(--text-4)", textAlign: "center", paddingTop: 20 }}>No functions registered.</div>
            )}
          </>
        )}
        {!loading && activeTab === "sets" && (
          <>
            {setsWithDetail.length === 0 ? (
              <div style={{ fontSize: 11, color: "var(--text-4)", textAlign: "center", paddingTop: 20 }}>No function sets.</div>
            ) : (
              setsWithDetail.map(s => <PaletteSetCard key={s.set_id} set={s} />)
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Side panel
// ---------------------------------------------------------------------------

function SidePanel({ source, onClose, onNavigate }) {
  const [pipeline, setPipeline] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [resultTags, setResultTags] = useState({});
  const [runningType, setRunningType] = useState(null); // null | "validations" | "transforms"
  const [runningSetId, setRunningSetId] = useState(null);
  // Pending step card state
  const [pendingStep, setPendingStep] = useState(null); // null | { dryRunResult, attachBody }
  const [pendingDryRunning, setPendingDryRunning] = useState(false);
  const [pendingSaving, setPendingSaving] = useState(false);
  const [pendingSaveError, setPendingSaveError] = useState(null);

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
    setRunningType(null);
    setRunningSetId(null);
    loadPipeline();
  }, [source && source.source_id]);

  function applyRunResults(steps, data) {
    const tags = {};
    if (data && Array.isArray(data.steps)) {
      data.steps.forEach(stepResult => {
        // match by set_id from the pipeline steps
        const matchingStep = steps.find(s => s.source_function_map_id === stepResult.source_function_map_id
          || s.set_name === stepResult.set_name);
        if (matchingStep) {
          tags[matchingStep.source_function_map_id] = deriveResultTag(stepResult);
        }
      });
    }
    setResultTags(prev => ({ ...prev, ...tags }));
  }

  function handleRunType(runType) {
    if (!pipeline || !pipeline.steps) return;
    setRunningType(runType);
    fetch("/pipelines/" + source.source_id + "/run?run_type=" + runType, { method: "POST" })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(data => {
        applyRunResults(pipeline.steps, data);
        setRunningType(null);
      })
      .catch(() => setRunningType(null));
  }

  function handleRunSet(setId) {
    if (!pipeline || !pipeline.steps) return;
    setRunningSetId(setId);
    fetch("/pipelines/" + source.source_id + "/run?run_type=set&set_id=" + setId, { method: "POST" })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(data => {
        applyRunResults(pipeline.steps, data);
        setRunningSetId(null);
      })
      .catch(() => setRunningSetId(null));
  }

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

  function handlePendingSave(bindings, scalarValues) {
    if (!pendingStep) return;
    setPendingSaving(true);
    setPendingSaveError(null);
    const body = { ...pendingStep.attachBody, bindings };
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

  const hasSteps = pipeline && pipeline.steps && pipeline.steps.length > 0;
  const sourceColumns = (pipeline && pipeline.source && pipeline.source.columns) || [];
  const btnBase = {
    flex: 1, padding: "7px 0", fontSize: 12, fontWeight: 600,
    borderRadius: "var(--radius)", border: "1px solid var(--border)",
    cursor: "pointer", transition: "opacity .15s",
  };

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

      {/* Run controls */}
      <div style={{
        padding: "10px 14px", borderBottom: "1px solid var(--border)",
        display: "flex", gap: 8, flexShrink: 0,
      }}>
        <button
          onClick={() => handleRunType("validations")}
          disabled={!hasSteps || runningType !== null}
          style={{
            ...btnBase,
            background: runningType === "validations" ? "var(--accent-soft)" : "var(--panel-2)",
            color: runningType === "validations" ? "var(--accent)" : "var(--text)",
            opacity: (!hasSteps || runningType !== null) ? 0.5 : 1,
          }}
        >
          {runningType === "validations" ? "Running..." : "Run Validations"}
        </button>
        <button
          onClick={() => handleRunType("transforms")}
          disabled={!hasSteps || runningType !== null}
          style={{
            ...btnBase,
            background: runningType === "transforms" ? "var(--accent-soft)" : "var(--panel-2)",
            color: runningType === "transforms" ? "var(--accent)" : "var(--text)",
            opacity: (!hasSteps || runningType !== null) ? 0.5 : 1,
          }}
        >
          {runningType === "transforms" ? "Running..." : "Run Transforms"}
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
        {pendingDryRunning && (
          <div style={{ color: "var(--text-4)", fontSize: 12, textAlign: "center", paddingBottom: 8 }}>Loading parameters...</div>
        )}
        {loading && (
          <div style={{ color: "var(--text-4)", fontSize: 13, textAlign: "center", paddingTop: 30 }}>Loading...</div>
        )}
        {error && (
          <div style={{ color: "#e05252", fontSize: 13 }}>{error}</div>
        )}
        {!loading && !error && pipeline && (
          <PipelineCanvas
            sourceId={source.source_id}
            steps={pipeline.steps}
            onReloadPipeline={loadPipeline}
            resultTags={resultTags}
            runningSetId={runningSetId}
            onRunSet={handleRunSet}
            onNavigateResults={() => onNavigate && onNavigate("results", { source_id: source.source_id })}
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
            sourceColumns={sourceColumns}
            onSave={handlePendingSave}
            onCancel={handlePendingCancel}
            saving={pendingSaving}
            saveError={pendingSaveError}
          />
        )}
      </div>
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
          />
        )}

        {/* Right panel — function / set palette */}
        <RightPalette selectedSource={selectedSource} />

      </div>
    </div>
  );
}

window.__ScreenBuilder__ = ScreenBuilder;
