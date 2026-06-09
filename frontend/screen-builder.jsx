// Report Builder screen — Phase E2: pipeline canvas with drag-to-reorder
const { useState, useEffect, useRef } = React;

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
// Step card
// ---------------------------------------------------------------------------

function StepCard({ step, sourceId, onRemoved, isDragging, onDragStart, onDragEnd, onDragOver }) {
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

function PipelineCanvas({ sourceId, steps, onReloadPipeline }) {
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
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Side panel
// ---------------------------------------------------------------------------

function SidePanel({ source, onClose }) {
  const [pipeline, setPipeline] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

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
    loadPipeline();
  }, [source && source.source_id]);

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

      <div style={{ flex: 1, overflow: "auto", padding: 14 }}>
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
          />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main screen
// ---------------------------------------------------------------------------

function ScreenBuilder({ flash }) {
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
          />
        )}

        {/* Right panel — palette placeholder (Slice 2) */}
        <div style={{
          width: 220, flexShrink: 0,
          borderLeft: "1px solid var(--border)",
          display: "flex", flexDirection: "column",
          overflow: "hidden",
        }}>
          <div style={{
            padding: "10px 14px",
            fontSize: 11, fontWeight: 600, color: "var(--text-3)",
            letterSpacing: ".05em", textTransform: "uppercase",
            borderBottom: "1px solid var(--border)",
          }}>
            Palette
          </div>
          <div style={{
            flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
            color: "var(--text-4)", fontSize: 12, padding: 16, textAlign: "center",
          }}>
            Function palette coming in Slice 2.
          </div>
        </div>

      </div>
    </div>
  );
}

window.__ScreenBuilder__ = ScreenBuilder;
