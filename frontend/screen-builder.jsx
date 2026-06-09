// Report Builder screen — Phase E1: read pipeline state
const { useState, useEffect } = React;

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

function PipelinePanel({ sourceId }) {
  const { Icon } = window.__UI__;
  const [pipeline, setPipeline] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!sourceId) { setPipeline(null); return; }
    setLoading(true);
    setError(null);
    fetch(`/pipelines/${sourceId}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(data => { setPipeline(data); setLoading(false); })
      .catch(err => { setError(`Failed to load pipeline (${err}).`); setLoading(false); });
  }, [sourceId]);

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
      <div style={{
        flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
        color: "var(--text-4)", fontSize: 13,
      }}>
        Loading…
      </div>
    );
  }

  if (error) {
    return (
      <div style={{
        flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
        color: "var(--text-4)", fontSize: 13,
      }}>
        {error}
      </div>
    );
  }

  if (!pipeline) return null;

  const { steps } = pipeline;

  return (
    <div style={{ flex: 1, overflow: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 12 }}>
      {steps.length === 0 ? (
        <div style={{
          flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
          color: "var(--text-4)", fontSize: 13, paddingTop: 60,
        }}>
          No pipeline steps attached to this source.
        </div>
      ) : (
        steps.map(step => <StepCard key={step.source_function_map_id} step={step} />)
      )}
    </div>
  );
}

function ScreenBuilder({ flash }) {
  const { SourceBadge } = window.__UI__;
  const [sources, setSources] = useState([]);
  const [selectedId, setSelectedId] = useState(null);

  useEffect(() => {
    fetch("/sources")
      .then(r => r.ok ? r.json() : [])
      .then(setSources)
      .catch(() => {});
  }, []);

  const selected = sources.find(s => s.source_id === selectedId) ?? null;

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* Header */}
      <div style={{
        padding: "16px 24px", borderBottom: "1px solid var(--border)", flexShrink: 0,
      }}>
        <div style={{ fontWeight: 600, fontSize: 16 }}>Report Builder</div>
        <div style={{ color: "var(--text-3)", fontSize: 12 }}>
          Select a source to inspect its pipeline
        </div>
      </div>

      {/* Body: two-panel layout */}
      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        {/* Left panel — source selector */}
        <div style={{
          width: 240, flexShrink: 0, borderRight: "1px solid var(--border)",
          overflow: "auto", display: "flex", flexDirection: "column",
        }}>
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
                onClick={() => setSelectedId(s.source_id)}
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
        </div>

        {/* Right panel — pipeline steps */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
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
          <PipelinePanel sourceId={selectedId} />
        </div>
      </div>
    </div>
  );
}

window.__ScreenBuilder__ = ScreenBuilder;
