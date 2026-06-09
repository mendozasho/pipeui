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
// Right palette — Functions + Sets tabs
// ---------------------------------------------------------------------------

function PaletteFunctionCard({ fn, onDragStart }) {
  return (
    <div
      draggable
      onDragStart={e => {
        e.dataTransfer.setData("palette/function_id", fn.function_id);
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
      // Lazy-fetch set details for type badge computation
      (Array.isArray(sts) ? sts : []).forEach(s => {
        fetch("/function-sets/" + s.set_id)
          .then(r => r.ok ? r.json() : null)
          .then(detail => {
            if (detail) {
              setSetsDetail(prev => ({ ...prev, [s.set_id]: detail }));
            }
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
      {/* Tab bar */}
      <div style={{
        display: "flex",
        borderBottom: "1px solid var(--border)",
        flexShrink: 0,
      }}>
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

function SidePanel({ source, onClose }) {
  const [pipeline, setPipeline] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [dropStatus, setDropStatus] = useState(null); // null | "attaching" | "ok" | "error"
  const [isDragOver, setIsDragOver] = useState(false);

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
    if (!functionId && !setId) return;

    const body = functionId
      ? { function_id: functionId, bindings: [] }
      : { set_id: setId, bindings: [] };

    setDropStatus("attaching");

    // dry-run first for suggestions (fire and forget — we ignore the result in v1)
    const dryRunBody = functionId ? { function_id: functionId } : { set_id: setId };
    fetch("/pipelines/" + source.source_id + "/steps?dry_run=true", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(dryRunBody),
    }).catch(() => {});

    // commit attach
    fetch("/pipelines/" + source.source_id + "/steps", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(data => {
        if (data.ok) {
          setDropStatus("ok");
          loadPipeline();
          setTimeout(() => setDropStatus(null), 1500);
        } else {
          setDropStatus("error");
          setTimeout(() => setDropStatus(null), 3000);
        }
      })
      .catch(() => {
        setDropStatus("error");
        setTimeout(() => setDropStatus(null), 3000);
      });
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
        {dropStatus === "attaching" && (
          <div style={{ color: "var(--text-4)", fontSize: 12, textAlign: "center", paddingBottom: 8 }}>Attaching…</div>
        )}
        {dropStatus === "ok" && (
          <div style={{ color: "var(--accent)", fontSize: 12, textAlign: "center", paddingBottom: 8 }}>Step added.</div>
        )}
        {dropStatus === "error" && (
          <div style={{ color: "#e05252", fontSize: 12, textAlign: "center", paddingBottom: 8 }}>Attach failed.</div>
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
          />
        )}
        {!loading && !error && pipeline && pipeline.steps.length === 0 && (
          <div style={{
            marginTop: 10, padding: "14px 10px", borderRadius: "var(--radius)",
            border: "2px dashed var(--border)", color: "var(--text-4)",
            fontSize: 12, textAlign: "center",
          }}>
            Drag a function or set here to add a pipeline step.
          </div>
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

        {/* Right panel — function / set palette */}
        <RightPalette selectedSource={selectedSource} />

      </div>
    </div>
  );
}

window.__ScreenBuilder__ = ScreenBuilder;
