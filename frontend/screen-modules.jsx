// Functions screen — Phase D: wired to real API data, with function detail drawer
const { useState, useEffect } = React;

// ---------------------------------------------------------------------------
// FunctionDrawer — detail drawer following the same pattern as SourceDrawer
// ---------------------------------------------------------------------------

function FnSection({ title, children }) {
  return (
    <div>
      <div style={{
        fontSize: 11, color: "var(--text-3)", fontWeight: 600,
        letterSpacing: ".05em", textTransform: "uppercase", marginBottom: 10,
      }}>{title}</div>
      {children}
    </div>
  );
}

function FnKV({ label, children }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", padding: "5px 0",
      borderBottom: "1px solid var(--border-soft)", fontSize: 13,
    }}>
      <span style={{ color: "var(--text-3)" }}>{label}</span>
      <span style={{ color: "var(--text)", fontWeight: 500 }}>{children}</span>
    </div>
  );
}

function FunctionDrawer({ functionId, onClose, flash }) {
  const { Drawer, KindTag } = window.__UI__;
  const [detail, setDetail] = useState(null);

  useEffect(() => {
    setDetail(null);
    if (!functionId) return;
    fetch(`/functions/${functionId}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(data => setDetail(data))
      .catch(() => flash && flash("Could not load function detail.", "error"));
  }, [functionId]);

  if (!functionId) return null;
  const fn = detail;

  return (
    <Drawer open={!!functionId} onClose={onClose} title={fn?.function_name ?? "…"} width={560}>
      {fn && (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>

          {/* Header: kind badge + active status */}
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <KindTag kind={fn.function_type} />
            <span style={{
              fontSize: 11, fontWeight: 600, padding: "2px 8px",
              borderRadius: 99,
              background: fn.is_active ? "var(--good-soft, rgba(0,200,100,.12))" : "var(--panel-3)",
              color: fn.is_active ? "var(--good, #00c864)" : "var(--text-3)",
              border: `1px solid ${fn.is_active ? "var(--good, #00c864)" : "var(--border)"}`,
            }}>
              {fn.is_active ? "active" : "inactive"}
            </span>
          </div>

          {/* Signature */}
          <FnSection title="Signature">
            <div style={{
              fontFamily: "'Geist Mono', monospace", fontSize: 12,
              background: "var(--panel-2)", border: "1px solid var(--border)",
              borderRadius: "var(--radius)", padding: "10px 14px",
              color: "var(--text)", wordBreak: "break-all",
            }}>
              {fn.function_name}{fn.function_signature}
            </div>
          </FnSection>

          {/* Docstring */}
          {fn.function_doc && (
            <FnSection title="Documentation">
              <div style={{
                fontSize: 13, color: "var(--text-2)", lineHeight: 1.6,
                whiteSpace: "pre-wrap",
              }}>
                {fn.function_doc}
              </div>
            </FnSection>
          )}

          {/* Parameters */}
          <FnSection title={`Parameters (${fn.parameters?.length ?? 0})`}>
            {(!fn.parameters || fn.parameters.length === 0) ? (
              <div style={{ color: "var(--text-3)", fontSize: 12, padding: "4px 0" }}>No parameters.</div>
            ) : (
              <div style={{ borderRadius: "var(--radius)", border: "1px solid var(--border)", overflow: "hidden" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, fontFamily: "'Geist Mono', monospace" }}>
                  <thead>
                    <tr style={{ background: "var(--panel-2)" }}>
                      <th style={{ padding: "6px 12px", textAlign: "left", fontWeight: 600, color: "var(--text-2)", borderBottom: "1px solid var(--border)" }}>param_name</th>
                      <th style={{ padding: "6px 12px", textAlign: "left", fontWeight: 600, color: "var(--text-2)", borderBottom: "1px solid var(--border)" }}>param_type</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fn.parameters.map((p, i) => (
                      <tr key={p.param_id} style={{ background: i % 2 === 0 ? "transparent" : "var(--panel-2)" }}>
                        <td style={{ padding: "5px 12px", borderBottom: "1px solid var(--border-soft)", color: "var(--text)" }}>{p.param_name}</td>
                        <td style={{ padding: "5px 12px", borderBottom: "1px solid var(--border-soft)", color: "var(--text-3)" }}>{p.param_type}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </FnSection>

          {/* Metadata */}
          <FnSection title="Details">
            <FnKV label="Return type">{fn.function_return_type}</FnKV>
            <FnKV label="Function type">{fn.function_type}</FnKV>
            <FnKV label="Function class">{fn.function_class}</FnKV>
            <FnKV label="Source file">
              <span style={{ fontFamily: "'Geist Mono', monospace", fontSize: 12, wordBreak: "break-all" }}>
                {fn.module_path}
              </span>
            </FnKV>
          </FnSection>

          {/* Attached sources */}
          <FnSection title="Attached to">
            {(!fn.attached_sources || fn.attached_sources.length === 0) ? (
              <div style={{ color: "var(--text-3)", fontSize: 12, padding: "4px 0" }}>
                Not attached to any sources yet.
              </div>
            ) : (
              fn.attached_sources.map(s => (
                <div key={s.source_id} style={{
                  padding: "6px 0", borderBottom: "1px solid var(--border-soft)",
                  fontSize: 13, color: "var(--text)",
                }}>
                  {s.source_name}
                </div>
              ))
            )}
          </FnSection>

        </div>
      )}
    </Drawer>
  );
}

// ---------------------------------------------------------------------------
// ScreenModules
// ---------------------------------------------------------------------------

function ScreenModules({ flash }) {
  const { KindTag, Icon, Btn } = window.__UI__;
  const [functions, setFunctions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [scanLog, setScanLog] = useState(null);
  const [scanOpen, setScanOpen] = useState(false);
  const [selectedFunction, setSelectedFunction] = useState(null);

  function loadFunctions() {
    return fetch("/functions")
      .then(r => r.json())
      .then(data => {
        setFunctions(data);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }

  useEffect(() => { loadFunctions(); }, []);

  function handleRescan() {
    setScanning(true);
    fetch("/functions/scan", { method: "POST" })
      .then(r => r.json())
      .then(data => {
        setScanLog(data.log || []);
        setScanOpen(true);
        setScanning(false);
        return loadFunctions();
      })
      .catch(() => setScanning(false));
  }

  // Group functions by module_path for display
  const byFile = {};
  for (const fn of functions) {
    const key = fn.module_path || "(unknown)";
    if (!byFile[key]) byFile[key] = [];
    byFile[key].push(fn);
  }

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* Header */}
      <div style={{
        padding: "16px 24px", borderBottom: "1px solid var(--border)",
        display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0,
      }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 16 }}>Functions</div>
          <div style={{ color: "var(--text-3)", fontSize: 12 }}>
            {loading ? "Loading…" : `${functions.length} function${functions.length !== 1 ? "s" : ""} registered`}
          </div>
        </div>
        <Btn icon="refresh" onClick={handleRescan} disabled={scanning}>
          {scanning ? "Scanning…" : "Rescan"}
        </Btn>
      </div>

      {/* Scan log section */}
      {scanLog && scanLog.length > 0 && (
        <div style={{
          margin: "12px 24px 0",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-lg)",
          overflow: "hidden",
          flexShrink: 0,
        }}>
          <div
            onClick={() => setScanOpen(o => !o)}
            style={{
              padding: "8px 14px",
              background: "var(--panel-2)",
              cursor: "pointer",
              display: "flex", alignItems: "center", gap: 8,
              fontSize: 12, fontWeight: 600,
            }}
          >
            <Icon name={scanOpen ? "chevron-down" : "chevron-right"} size={12} />
            Last scan — {scanLog.length} entr{scanLog.length !== 1 ? "ies" : "y"}
          </div>
          {scanOpen && (
            <div style={{ maxHeight: 200, overflow: "auto" }}>
              {scanLog.map((entry, i) => (
                <div key={i} style={{
                  padding: "6px 14px",
                  borderTop: "1px solid var(--border-soft)",
                  display: "flex", gap: 8, alignItems: "baseline",
                  fontSize: 12,
                }}>
                  <span style={{
                    fontFamily: "'Geist Mono', monospace",
                    color: entry.status === "added" ? "var(--good)"
                      : entry.status === "re-registered" ? "var(--accent)"
                      : "var(--text-3)",
                    fontWeight: 600, minWidth: 90,
                  }}>
                    {entry.status.startsWith("skipped") ? "skipped" : entry.status}
                  </span>
                  <span style={{ fontFamily: "'Geist Mono', monospace", color: "var(--text-2)" }}>
                    {entry.function_name}
                  </span>
                  <span style={{ color: "var(--text-4)", flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>
                    {entry.file}
                  </span>
                  {entry.status.startsWith("skipped:") && (
                    <span style={{ color: "var(--bad)", fontSize: 11 }}>
                      {entry.status.slice("skipped: ".length)}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Function list */}
      <div style={{ flex: 1, overflow: "auto", padding: 24 }}>
        {loading && (
          <div style={{ color: "var(--text-3)", fontSize: 13 }}>Loading functions…</div>
        )}
        {!loading && functions.length === 0 && (
          <div style={{ color: "var(--text-3)", fontSize: 13 }}>
            No functions registered yet. Add a directory in Settings → functions_paths and press Rescan.
          </div>
        )}
        {!loading && Object.entries(byFile).map(([filePath, fns]) => (
          <div key={filePath} style={{
            background: "var(--panel)", border: "1px solid var(--border)",
            borderRadius: "var(--radius-lg)", marginBottom: 12, overflow: "hidden",
          }}>
            {/* File header */}
            <div style={{
              padding: "10px 16px", borderBottom: "1px solid var(--border-soft)",
              display: "flex", alignItems: "center", gap: 8,
            }}>
              <Icon name="file" size={14} style={{ color: "var(--text-3)" }} />
              <span style={{ fontWeight: 600, fontFamily: "'Geist Mono', monospace", fontSize: 13 }}>
                {filePath.split("/").pop()}
              </span>
              <span style={{ fontSize: 11, color: "var(--text-4)", flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>
                {filePath}
              </span>
              <span style={{ marginLeft: 8, fontSize: 11, color: "var(--text-3)", flexShrink: 0 }}>
                {fns.length} function{fns.length !== 1 ? "s" : ""}
              </span>
            </div>
            {/* Function cards — clickable to open drawer */}
            {fns.map(fn => (
              <div
                key={fn.function_id}
                onClick={() => setSelectedFunction(fn)}
                style={{
                  padding: "12px 16px", display: "flex", alignItems: "flex-start", gap: 12,
                  borderBottom: "1px solid var(--border-soft)",
                  cursor: "pointer",
                  background: selectedFunction?.function_id === fn.function_id
                    ? "var(--accent-soft)"
                    : "transparent",
                  transition: "background .1s",
                }}
              >
                <KindTag kind={fn.function_type} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 500, marginBottom: 2 }}>{fn.function_name}</div>
                  {fn.function_doc && (
                    <div style={{ fontSize: 12, color: "var(--text-3)", marginBottom: 4 }}>
                      {fn.function_doc.split("\n")[0]}
                    </div>
                  )}
                  <div style={{ fontSize: 11, color: "var(--text-4)", fontFamily: "'Geist Mono', monospace" }}>
                    {fn.function_name}{fn.function_signature}
                  </div>
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>

      {/* Function detail drawer */}
      <FunctionDrawer
        functionId={selectedFunction?.function_id}
        onClose={() => setSelectedFunction(null)}
        flash={flash}
      />
    </div>
  );
}

window.__ScreenModules__ = ScreenModules;
