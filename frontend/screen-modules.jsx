// Functions screen — Phase D: wired to real API data
const { useState, useEffect } = React;

function ScreenModules() {
  const { KindTag, Icon, Btn } = window.__UI__;
  const [functions, setFunctions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [scanLog, setScanLog] = useState(null);
  const [scanOpen, setScanOpen] = useState(false);

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
                      : entry.status === "file_missing" ? "var(--warn, #e8a020)"
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
                  {entry.status === "file_missing" && (
                    <span style={{ color: "var(--warn, #e8a020)", fontSize: 11 }}>
                      file no longer on disk
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
            {/* Function cards */}
            {fns.map(fn => (
              <div key={fn.function_id} style={{
                padding: "12px 16px", display: "flex", alignItems: "flex-start", gap: 12,
                borderBottom: "1px solid var(--border-soft)",
                opacity: fn.is_active ? 1 : 0.5,
              }}>
                <KindTag kind={fn.function_type} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 500, marginBottom: 2, display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ color: fn.is_active ? undefined : "var(--text-3)" }}>{fn.function_name}</span>
                    {!fn.is_active && (
                      <span style={{
                        fontSize: 10, fontWeight: 600, padding: "1px 6px",
                        borderRadius: 4, background: "var(--panel-2)",
                        color: "var(--text-3)", border: "1px solid var(--border)",
                        letterSpacing: "0.03em",
                      }}>
                        Unavailable
                      </span>
                    )}
                  </div>
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
    </div>
  );
}

window.__ScreenModules__ = ScreenModules;
