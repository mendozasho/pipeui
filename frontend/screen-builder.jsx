// Report Builder screen — Phase E1: read + attach pipeline steps
const { useState, useEffect, useRef } = React;

// ---------------------------------------------------------------------------
// Committed step display components
// ---------------------------------------------------------------------------

function ParamRow({ param }) {
  const isDataFrame = param.param_type === "pd.DataFrame";
  return (
    <div style={{
      padding: "6px 0",
      borderBottom: "1px solid var(--border-soft)",
      display: "flex",
      flexDirection: "column",
      gap: 3,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontFamily: "'Geist Mono', monospace", fontSize: 12, color: "var(--text)" }}>
          {param.param_name}
        </span>
        <span style={{
          fontSize: 11, padding: "1px 6px", borderRadius: 99,
          background: "var(--panel-3)", color: "var(--text-3)",
          fontFamily: "'Geist Mono', monospace",
        }}>
          {param.param_type}
        </span>
      </div>
      {isDataFrame ? (
        <span style={{ fontSize: 11, color: "var(--text-4)", fontStyle: "italic" }}>
          auto (full table)
        </span>
      ) : param.bindings.length > 0 ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          {param.bindings.map(b => (
            <span key={b.column_id} style={{
              fontSize: 11, padding: "1px 7px", borderRadius: 99,
              background: "var(--accent-soft)", color: "var(--accent)",
              fontFamily: "'Geist Mono', monospace",
            }}>
              {b.column_name}
            </span>
          ))}
        </div>
      ) : (
        <span style={{ fontSize: 11, color: "var(--text-4)", fontStyle: "italic" }}>
          unbound
        </span>
      )}
    </div>
  );
}

function FunctionCard({ fn }) {
  return (
    <div style={{
      background: "var(--panel-2)", borderRadius: "var(--radius)",
      border: "1px solid var(--border)", padding: "12px 14px",
      display: "flex", flexDirection: "column", gap: 8,
    }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span style={{ fontWeight: 600, fontSize: 13 }}>{fn.function_name}</span>
        <span style={{
          fontSize: 11, padding: "1px 6px", borderRadius: 99,
          background: "var(--panel-3)", color: "var(--text-3)",
          fontFamily: "'Geist Mono', monospace",
        }}>
          {fn.function_type}
        </span>
      </div>
      {fn.function_doc && (
        <div style={{ fontSize: 12, color: "var(--text-3)", lineHeight: 1.5 }}>
          {fn.function_doc}
        </div>
      )}
      {fn.params.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column" }}>
          {fn.params.map(p => <ParamRow key={p.param_id} param={p} />)}
        </div>
      )}
    </div>
  );
}

function StepCard({ step }) {
  return (
    <div style={{
      background: "var(--panel)", border: "1px solid var(--border)",
      borderRadius: "var(--radius-lg)", padding: "14px 16px",
      display: "flex", flexDirection: "column", gap: 10,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{
          fontSize: 11, fontWeight: 700,
          background: "var(--panel-3)", color: "var(--text-3)",
          borderRadius: 99, padding: "2px 8px",
        }}>
          {step.position !== null ? `#${step.position}` : "—"}
        </span>
        <span style={{ fontWeight: 600, fontSize: 13 }}>{step.set_name}</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {step.functions.map(fn => <FunctionCard key={fn.function_id} fn={fn} />)}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pending step card — drag-and-drop binding UI
// ---------------------------------------------------------------------------

const REQUIRES_BINDING = new Set(["str", "column_backed", "pd.Series"]);

function PendingParamRow({ param, boundColumns, onDrop, onRemove }) {
  const isDataFrame = param.param_type === "pd.DataFrame";
  const isScalar = !isDataFrame && !REQUIRES_BINDING.has(param.param_type);
  const [dragOver, setDragOver] = useState(false);

  if (isDataFrame) {
    return (
      <div style={{ padding: "6px 0", borderBottom: "1px solid var(--border-soft)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontFamily: "'Geist Mono', monospace", fontSize: 12 }}>{param.param_name}</span>
          <span style={{ fontSize: 11, padding: "1px 6px", borderRadius: 99, background: "var(--panel-3)", color: "var(--text-3)", fontFamily: "'Geist Mono', monospace" }}>
            {param.param_type}
          </span>
        </div>
        <span style={{ fontSize: 11, color: "var(--text-4)", fontStyle: "italic" }}>auto (full table)</span>
      </div>
    );
  }

  if (isScalar) {
    return (
      <div style={{ padding: "6px 0", borderBottom: "1px solid var(--border-soft)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontFamily: "'Geist Mono', monospace", fontSize: 12 }}>{param.param_name}</span>
          <span style={{ fontSize: 11, padding: "1px 6px", borderRadius: 99, background: "var(--panel-3)", color: "var(--text-3)", fontFamily: "'Geist Mono', monospace" }}>
            {param.param_type}
          </span>
        </div>
        <span style={{ fontSize: 11, color: "var(--text-4)", fontStyle: "italic" }}>scalar (uses default)</span>
      </div>
    );
  }

  // column_backed / pd.Series — multi-column drop zone
  return (
    <div style={{ padding: "6px 0", borderBottom: "1px solid var(--border-soft)", display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontFamily: "'Geist Mono', monospace", fontSize: 12 }}>{param.param_name}</span>
        <span style={{ fontSize: 11, padding: "1px 6px", borderRadius: 99, background: "var(--panel-3)", color: "var(--text-3)", fontFamily: "'Geist Mono', monospace" }}>
          {param.param_type}
        </span>
        <span style={{ fontSize: 10, color: "var(--text-4)" }}>drag columns here</span>
      </div>
      <div
        onDragOver={e => { e.preventDefault(); e.stopPropagation(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={e => {
          e.preventDefault();
          e.stopPropagation();
          setDragOver(false);
          const raw = e.dataTransfer.getData("application/json");
          if (!raw) return;
          const data = JSON.parse(raw);
          if (data && data.type === "column") onDrop(param.param_id, data.column);
        }}
        style={{
          minHeight: 32,
          border: `1.5px dashed ${dragOver ? "var(--accent)" : "var(--border)"}`,
          borderRadius: "var(--radius)",
          background: dragOver ? "var(--accent-soft)" : "var(--panel-2)",
          display: "flex", flexWrap: "wrap", gap: 4, padding: "4px 6px",
          transition: "border-color .1s, background .1s",
        }}
      >
        {boundColumns.length === 0 ? (
          <span style={{ fontSize: 11, color: "var(--text-4)", alignSelf: "center" }}>drop columns here</span>
        ) : (
          boundColumns.map(col => (
            <span
              key={col.column_id}
              onClick={() => onRemove(param.param_id, col.column_id)}
              title="Click to remove"
              style={{
                fontSize: 11, padding: "1px 7px", borderRadius: 99,
                background: "var(--accent-soft)", color: "var(--accent)",
                fontFamily: "'Geist Mono', monospace",
                cursor: "pointer",
              }}
            >
              {col.column_name} ×
            </span>
          ))
        )}
      </div>
    </div>
  );
}

function PendingStepCard({ item, sourceColumns, initialBindings, onSave, onCancel }) {
  const [bindings, setBindings] = useState(initialBindings || {});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);

  const requiredParams = (item.params || []).filter(p => REQUIRES_BINDING.has(p.param_type));
  const allBound = requiredParams.every(p => (bindings[p.param_id] || []).length > 0);

  function handleDrop(paramId, column) {
    setBindings(prev => {
      const existing = prev[paramId] || [];
      if (existing.find(c => c.column_id === column.column_id)) return prev;
      return { ...prev, [paramId]: [...existing, column] };
    });
  }

  function handleRemove(paramId, columnId) {
    setBindings(prev => ({
      ...prev,
      [paramId]: (prev[paramId] || []).filter(c => c.column_id !== columnId),
    }));
  }

  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    const body = {
      bindings: Object.entries(bindings).map(([param_id, cols]) => ({
        param_id,
        column_ids: cols.map(c => c.column_id),
      })),
    };
    if (item.type === "function") body.function_id = item.id;
    else body.set_id = item.id;

    try {
      const resp = await fetch(item.postUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await resp.json();
      if (data.ok) {
        onSave();
      } else {
        const names = (data.missing_params || []).map(p => p.param_name).join(", ");
        setSaveError(data.detail + (names ? `: ${names}` : ""));
        setSaving(false);
      }
    } catch (e) {
      setSaveError(`Network error: ${e.message}`);
      setSaving(false);
    }
  }

  return (
    <div style={{
      background: "var(--panel)", border: "2px dashed var(--accent)",
      borderRadius: "var(--radius-lg)", padding: "14px 16px",
      display: "flex", flexDirection: "column", gap: 10,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{
          fontSize: 11, fontWeight: 700,
          background: "var(--accent-soft)", color: "var(--accent)",
          borderRadius: 99, padding: "2px 8px",
        }}>pending</span>
        <span style={{ fontWeight: 600, fontSize: 13 }}>{item.name}</span>
        <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-4)" }}>
          {item.type === "function" ? "function" : "set"}
        </span>
      </div>

      {(item.params || []).length > 0 && (
        <div style={{ display: "flex", flexDirection: "column" }}>
          {item.params.map(p => (
            <PendingParamRow
              key={p.param_id}
              param={p}
              boundColumns={bindings[p.param_id] || []}
              onDrop={handleDrop}
              onRemove={handleRemove}
            />
          ))}
        </div>
      )}

      {saveError && (
        <div style={{ fontSize: 12, color: "#e05252", background: "#fff0f0", borderRadius: "var(--radius)", padding: "6px 10px" }}>
          {saveError}
        </div>
      )}

      <div style={{ display: "flex", gap: 8 }}>
        <button
          onClick={handleSave}
          disabled={!allBound || saving}
          style={{
            padding: "5px 14px", fontSize: 12, borderRadius: "var(--radius)",
            border: "none", cursor: allBound && !saving ? "pointer" : "not-allowed",
            background: allBound && !saving ? "var(--accent)" : "var(--panel-3)",
            color: allBound && !saving ? "#fff" : "var(--text-4)",
            fontWeight: 600,
          }}
        >
          {saving ? "Saving…" : "Save step"}
        </button>
        <button
          onClick={onCancel}
          style={{
            padding: "5px 12px", fontSize: 12, borderRadius: "var(--radius)",
            border: "1px solid var(--border)", background: "transparent",
            cursor: "pointer", color: "var(--text-3)",
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pipeline panel
// ---------------------------------------------------------------------------

function PipelinePanel({ sourceId, sourceColumns, onColumnsLoaded }) {
  const { Icon } = window.__UI__;
  const [pipeline, setPipeline] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [pendingSteps, setPendingSteps] = useState([]);

  function loadPipeline() {
    if (!sourceId) { setPipeline(null); return; }
    setLoading(true);
    setError(null);
    fetch(`/pipelines/${sourceId}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(data => {
        setPipeline(data);
        setLoading(false);
        if (onColumnsLoaded) onColumnsLoaded(data.source.columns || []);
      })
      .catch(err => { setError(`Failed to load pipeline (${err}).`); setLoading(false); });
  }

  useEffect(() => {
    loadPipeline();
    setPendingSteps([]);
  }, [sourceId]);

  function addPendingStep(item) {
    setPendingSteps(prev => [...prev, { ...item, key: Math.random().toString(36).slice(2) }]);
  }
  function removePendingStep(key) {
    setPendingSteps(prev => prev.filter(s => s.key !== key));
  }
  function handleSaved(key) {
    removePendingStep(key);
    loadPipeline();
  }

  // Expose addPendingStep so the drag-drop handler in the parent can call it
  window.__builderAddStep__ = addPendingStep;

  if (!sourceId) {
    return (
      <div style={{
        flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
        color: "var(--text-4)",
      }}>
        <div style={{ textAlign: "center" }}>
          <Icon name="builder" size={36} style={{ marginBottom: 10, opacity: 0.25 }} />
          <div style={{ fontSize: 13 }}>Select a source to view its pipeline</div>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-4)", fontSize: 13 }}>
        Loading…
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-4)", fontSize: 13 }}>
        {error}
      </div>
    );
  }

  if (!pipeline) return null;

  const { steps } = pipeline;

  return (
    <div style={{ flex: 1, overflow: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 12 }}>
      {steps.length === 0 && pendingSteps.length === 0 ? (
        <div style={{
          flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
          color: "var(--text-4)", fontSize: 13, paddingTop: 60,
        }}>
          No pipeline steps — drag a function or set from the palette.
        </div>
      ) : (
        <>
          {steps.map(step => <StepCard key={step.source_function_map_id} step={step} />)}
          {pendingSteps.map(item => (
            <PendingStepCard
              key={item.key}
              item={item}
              sourceColumns={sourceColumns}
              initialBindings={item.initialBindings || {}}
              onSave={() => handleSaved(item.key)}
              onCancel={() => removePendingStep(item.key)}
            />
          ))}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Function / set palette
// ---------------------------------------------------------------------------

function PaletteItem({ label, sublabel, onDragStart }) {
  return (
    <div
      draggable
      onDragStart={onDragStart}
      style={{
        padding: "8px 12px", cursor: "grab",
        borderBottom: "1px solid var(--border-soft)",
        display: "flex", flexDirection: "column", gap: 2,
        userSelect: "none",
      }}
      title="Drag onto pipeline to attach"
    >
      <span style={{ fontSize: 12, fontWeight: 500, color: "var(--text)" }}>{label}</span>
      {sublabel && (
        <span style={{ fontSize: 10, color: "var(--text-4)", fontFamily: "'Geist Mono', monospace" }}>
          {sublabel}
        </span>
      )}
    </div>
  );
}

function Palette({ sourceId, functions, functionSets }) {
  if (!sourceId) return null;

  function makeFnDrag(fn) {
    return e => {
      e.dataTransfer.setData("application/json", JSON.stringify({
        type: "palette",
        item: {
          type: "function",
          id: fn.function_id,
          name: fn.function_name,
          params: (fn.params || []).map(p => ({
            param_id: p.param_id,
            param_name: p.param_name,
            param_type: p.param_type,
          })),
          postUrl: `/pipelines/${sourceId}/steps`,
        },
      }));
    };
  }

  function makeSetDrag(fs) {
    return e => {
      const params = (fs.functions || []).flatMap(fn =>
        (fn.params || []).map(p => ({
          param_id: p.param_id,
          param_name: p.param_name,
          param_type: p.param_type,
        }))
      );
      e.dataTransfer.setData("application/json", JSON.stringify({
        type: "palette",
        item: {
          type: "set",
          id: fs.set_id,
          name: fs.set_name,
          params,
          postUrl: `/pipelines/${sourceId}/steps`,
        },
      }));
    };
  }

  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      <div style={{
        padding: "10px 14px",
        fontSize: 11, fontWeight: 600, color: "var(--text-3)",
        letterSpacing: ".05em", textTransform: "uppercase",
        borderBottom: "1px solid var(--border)",
        borderTop: "1px solid var(--border)",
      }}>
        Functions
      </div>
      {functions.length === 0 ? (
        <div style={{ padding: "10px 14px", fontSize: 12, color: "var(--text-4)" }}>
          No functions registered.
        </div>
      ) : (
        functions.map(fn => (
          <PaletteItem
            key={fn.function_id}
            label={fn.function_name}
            sublabel={fn.function_type}
            onDragStart={makeFnDrag(fn)}
          />
        ))
      )}
      {functionSets.length > 0 && (
        <>
          <div style={{
            padding: "10px 14px",
            fontSize: 11, fontWeight: 600, color: "var(--text-3)",
            letterSpacing: ".05em", textTransform: "uppercase",
            borderBottom: "1px solid var(--border)",
            borderTop: "1px solid var(--border)",
          }}>
            Sets
          </div>
          {functionSets.map(fs => (
            <PaletteItem
              key={fs.set_id}
              label={fs.set_name}
              sublabel={`${(fs.functions || []).length} fn`}
              onDragStart={makeSetDrag(fs)}
            />
          ))}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main screen
// ---------------------------------------------------------------------------

function ScreenBuilder({ flash }) {
  const { SourceBadge } = window.__UI__;
  const [sources, setSources] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [functions, setFunctions] = useState([]);
  const [functionSets, setFunctionSets] = useState([]);
  const [sourceColumns, setSourceColumns] = useState([]);

  useEffect(() => {
    fetch("/sources")
      .then(r => r.ok ? r.json() : [])
      .then(setSources)
      .catch(() => {});
    fetch("/functions")
      .then(r => r.ok ? r.json() : [])
      .then(setFunctions)
      .catch(() => {});
    fetch("/function-sets")
      .then(r => r.ok ? r.json() : [])
      .then(setFunctionSets)
      .catch(() => {});
  }, []);

  const selected = sources.find(s => s.source_id === selectedId) ?? null;

  function handleDragOverPipeline(e) { e.preventDefault(); }
  async function handleDropPipeline(e) {
    e.preventDefault();
    const raw = e.dataTransfer.getData("application/json");
    if (!raw) return;
    let data;
    try {
      data = JSON.parse(raw);
    } catch (_) {
      return;
    }
    if (data.type !== "palette" || typeof window.__builderAddStep__ !== "function") return;
    const item = data.item;

    // Fire dry-run first to pre-fill suggested bindings
    let initialBindings = {};
    if (selectedId) {
      try {
        const body = item.type === "function"
          ? { function_id: item.id }
          : { set_id: item.id };
        const resp = await fetch(`/pipelines/${selectedId}/steps?dry_run=true`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (resp.ok) {
          const suggest = await resp.json();
          // Build initialBindings: param_id -> [{ column_id, column_name }]
          for (const p of (suggest.params || [])) {
            if ((p.suggested_columns || []).length > 0) {
              initialBindings[p.param_id] = p.suggested_columns;
            }
          }
        }
      } catch (_) {
        // Dry-run failure is non-fatal; proceed without suggestions
      }
    }

    window.__builderAddStep__({ ...item, initialBindings });
  }

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* Header */}
      <div style={{
        padding: "16px 24px", borderBottom: "1px solid var(--border)", flexShrink: 0,
      }}>
        <div style={{ fontWeight: 600, fontSize: 16 }}>Report Builder</div>
        <div style={{ color: "var(--text-3)", fontSize: 12 }}>
          Select a source, then drag functions from the palette onto the pipeline
        </div>
      </div>

      {/* Body: two-panel layout */}
      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        {/* Left panel — source selector + columns + function palette */}
        <div style={{
          width: 240, flexShrink: 0, borderRight: "1px solid var(--border)",
          overflow: "auto", display: "flex", flexDirection: "column",
        }}>
          {/* Sources */}
          <div style={{
            padding: "10px 14px",
            fontSize: 11, fontWeight: 600, color: "var(--text-3)",
            letterSpacing: ".05em", textTransform: "uppercase",
            borderBottom: "1px solid var(--border)",
          }}>
            Sources
          </div>
          {sources.length === 0 ? (
            <div style={{ padding: "16px 14px", fontSize: 12, color: "var(--text-4)" }}>
              No sources registered.
            </div>
          ) : (
            sources.map(s => (
              <div
                key={s.source_id}
                onClick={() => { setSelectedId(s.source_id); setSourceColumns([]); }}
                style={{
                  padding: "10px 14px",
                  cursor: "pointer",
                  background: s.source_id === selectedId ? "var(--accent-soft)" : "transparent",
                  borderBottom: "1px solid var(--border-soft)",
                  display: "flex", alignItems: "center", gap: 9,
                  transition: "background .1s",
                }}
              >
                <SourceBadge name={s.source_name} />
                <span style={{
                  fontSize: 13,
                  fontWeight: s.source_id === selectedId ? 600 : 400,
                  color: s.source_id === selectedId ? "var(--accent)" : "var(--text)",
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>
                  {s.source_name}
                </span>
              </div>
            ))
          )}

          {/* Columns (draggable) — only when a source is selected */}
          {selected && sourceColumns.length > 0 && (
            <>
              <div style={{
                padding: "10px 14px",
                fontSize: 11, fontWeight: 600, color: "var(--text-3)",
                letterSpacing: ".05em", textTransform: "uppercase",
                borderBottom: "1px solid var(--border)",
                borderTop: "1px solid var(--border)",
              }}>
                Columns
              </div>
              {sourceColumns.map(col => (
                <div
                  key={col.column_id}
                  draggable
                  onDragStart={e => {
                    e.dataTransfer.setData("application/json", JSON.stringify({
                      type: "column",
                      column: { column_id: col.column_id, column_name: col.column_name },
                    }));
                  }}
                  style={{
                    padding: "6px 14px", cursor: "grab",
                    borderBottom: "1px solid var(--border-soft)",
                    display: "flex", alignItems: "center", gap: 6,
                    userSelect: "none",
                  }}
                  title="Drag onto a parameter binding zone"
                >
                  <span style={{ fontSize: 12, fontFamily: "'Geist Mono', monospace", color: "var(--text)" }}>
                    {col.column_name}
                  </span>
                  <span style={{ fontSize: 10, color: "var(--text-4)" }}>{col.column_type}</span>
                </div>
              ))}
            </>
          )}

          {/* Function + set palette */}
          <Palette
            sourceId={selectedId}
            functions={functions}
            functionSets={functionSets}
          />
        </div>

        {/* Right panel — pipeline steps (drop target) */}
        <div
          style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}
          onDragOver={handleDragOverPipeline}
          onDrop={handleDropPipeline}
        >
          {selected && (
            <div style={{
              padding: "10px 16px",
              borderBottom: "1px solid var(--border)",
              fontSize: 13, fontWeight: 500,
              display: "flex", alignItems: "center", gap: 8,
            }}>
              <SourceBadge name={selected.source_name} />
              {selected.source_name}
            </div>
          )}
          <PipelinePanel
            sourceId={selectedId}
            sourceColumns={sourceColumns}
            onColumnsLoaded={setSourceColumns}
          />
        </div>
      </div>
    </div>
  );
}

window.__ScreenBuilder__ = ScreenBuilder;
