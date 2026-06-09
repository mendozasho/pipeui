// Results screen — Phase F1: Validations skeleton
const { useState, useEffect, useCallback } = React;

// ── Status badge for pass/fail ────────────────────────────────────────────────
function ValidationBadge({ status }) {
  const ok = status === "ok";
  return (
    <span style={{
      display: "inline-block", padding: "2px 8px", borderRadius: 99,
      background: ok ? "rgba(52,211,153,.1)" : "rgba(248,113,113,.1)",
      color: ok ? "var(--good)" : "var(--bad)",
      fontSize: 11, fontWeight: 600, letterSpacing: ".03em",
    }}>
      {ok ? "pass" : "fail"}
    </span>
  );
}

// ── CSV export helper ─────────────────────────────────────────────────────────
function sanitiseFilename(str) {
  return str.replace(/[^a-zA-Z0-9_-]/g, "_");
}

function exportCsv(rows, sourceName, functionName) {
  if (!rows || rows.length === 0) return;
  const filename = `${sanitiseFilename(sourceName)}_${sanitiseFilename(functionName)}_failures.csv`;
  const cols = Object.keys(rows[0]);
  const lines = [cols.join(",")];
  for (const row of rows) {
    lines.push(cols.map(c => {
      const v = row[c];
      if (v === null || v === undefined) return "";
      const s = String(v);
      // Quote if contains comma, newline, or quote
      if (s.includes(",") || s.includes("\n") || s.includes('"')) {
        return '"' + s.replace(/"/g, '""') + '"';
      }
      return s;
    }).join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ── Failing rows expandable detail ───────────────────────────────────────────
const PREVIEW_CAP = 200;

function FailingRowsDetail({ row, sourceName }) {
  const { failing_rows } = row;
  if (!failing_rows || failing_rows.length === 0) return null;

  const preview = failing_rows.slice(0, PREVIEW_CAP);
  const cols = Object.keys(preview[0]);

  const thStyle = {
    padding: "4px 10px", textAlign: "left", fontSize: 11, fontWeight: 600,
    color: "var(--text-3)", borderBottom: "1px solid var(--border)",
    whiteSpace: "nowrap", background: "var(--panel-2)",
  };
  const tdStyle = {
    padding: "4px 10px", fontSize: 12, borderBottom: "1px solid var(--border)",
    whiteSpace: "nowrap", color: "var(--text-2)",
  };

  return (
    <tr>
      <td colSpan={5} style={{ padding: "0 0 0 32px", background: "var(--panel-2)" }}>
        <div style={{ padding: "10px 12px 12px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text-3)" }}>
              {failing_rows.length > PREVIEW_CAP
                ? `Showing ${PREVIEW_CAP} of ${failing_rows.length.toLocaleString()} failing rows`
                : `${failing_rows.length.toLocaleString()} failing row${failing_rows.length !== 1 ? "s" : ""}`}
            </span>
            <button
              onClick={() => exportCsv(failing_rows, sourceName, row.function_name)}
              style={{
                padding: "3px 10px", fontSize: 11, fontWeight: 600,
                background: "var(--accent)", color: "#fff",
                border: "none", borderRadius: "var(--radius)", cursor: "pointer",
              }}
            >
              Export CSV
            </button>
          </div>
          <div style={{ overflowX: "auto", maxWidth: "100%" }}>
            <table style={{ borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr>
                  {cols.map(c => <th key={c} style={thStyle}>{c}</th>)}
                </tr>
              </thead>
              <tbody>
                {preview.map((r, i) => (
                  <tr key={i} style={{ background: i % 2 === 0 ? "var(--panel)" : "var(--panel-2)" }}>
                    {cols.map(c => (
                      <td key={c} style={tdStyle}>
                        {r[c] === null || r[c] === undefined ? <span style={{ color: "var(--text-4)" }}>null</span> : String(r[c])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </td>
    </tr>
  );
}

// ── Results table grouped by set_name ─────────────────────────────────────────
function ValidationResultsTable({ steps, sourceName }) {
  const [expanded, setExpanded] = useState({});

  // Group by set_name, preserving order of first appearance
  const groups = [];
  const seen = new Map();
  for (const step of steps) {
    if (!seen.has(step.set_name)) {
      const g = { set_name: step.set_name, set_id: step.set_id, rows: [] };
      seen.set(step.set_name, g);
      groups.push(g);
    }
    seen.get(step.set_name).rows.push(step);
  }

  const thStyle = {
    padding: "6px 12px", textAlign: "left", fontSize: 11, fontWeight: 600,
    color: "var(--text-3)", borderBottom: "1px solid var(--border)",
    whiteSpace: "nowrap",
  };
  const tdStyle = {
    padding: "8px 12px", fontSize: 13, borderBottom: "1px solid var(--border)",
    verticalAlign: "middle",
  };

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr>
            <th style={{ ...thStyle, width: 24 }}></th>
            <th style={thStyle}>Function</th>
            <th style={thStyle}>Status</th>
            <th style={{ ...thStyle, textAlign: "right" }}>Passed</th>
            <th style={{ ...thStyle, textAlign: "right" }}>Failed</th>
            <th style={{ ...thStyle, textAlign: "right" }}>Pass rate</th>
          </tr>
        </thead>
        <tbody>
          {groups.map(group => (
            <React.Fragment key={group.set_id || group.set_name}>
              {/* Set group header row */}
              <tr>
                <td colSpan={6} style={{
                  padding: "10px 12px 4px", fontSize: 11, fontWeight: 700,
                  color: "var(--text-3)", letterSpacing: ".05em", textTransform: "uppercase",
                  background: "var(--panel-2)", borderBottom: "1px solid var(--border)",
                }}>
                  {group.set_name}
                </td>
              </tr>
              {group.rows.map((row, i) => {
                const key = row.function_id + i;
                const isExpanded = !!expanded[key];
                const hasFailingRows = row.failing_rows && row.failing_rows.length > 0;
                return (
                  <React.Fragment key={key}>
                    <tr
                      style={{ background: "var(--panel)", cursor: hasFailingRows ? "pointer" : "default" }}
                      onClick={() => {
                        if (hasFailingRows) setExpanded(prev => ({ ...prev, [key]: !prev[key] }));
                      }}
                    >
                      <td style={{ ...tdStyle, width: 24, paddingRight: 0, color: "var(--text-4)", fontSize: 11 }}>
                        {hasFailingRows ? (isExpanded ? "▾" : "▸") : ""}
                      </td>
                      <td style={tdStyle}>
                        <span style={{ fontWeight: 500 }}>{row.function_name}</span>
                        {row.status === "failed" && row.error && (
                          <div style={{ color: "var(--bad)", fontSize: 11, marginTop: 3 }}>
                            {row.error}
                          </div>
                        )}
                      </td>
                      <td style={tdStyle}>
                        <ValidationBadge status={row.status} />
                      </td>
                      <td style={{ ...tdStyle, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                        {row.rows_passed !== null ? row.rows_passed.toLocaleString() : "—"}
                      </td>
                      <td style={{ ...tdStyle, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                        {row.rows_failed !== null ? row.rows_failed.toLocaleString() : "—"}
                      </td>
                      <td style={{ ...tdStyle, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                        {row.pass_rate !== null && row.pass_rate !== undefined
                          ? `${(row.pass_rate * 100).toFixed(1)}%`
                          : "—"}
                      </td>
                    </tr>
                    {isExpanded && (
                      <FailingRowsDetail row={row} sourceName={sourceName} />
                    )}
                  </React.Fragment>
                );
              })}
            </React.Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────
function ScreenResults({ flash, validationResults, setValidationResults }) {
  const { Btn } = window.__UI__;

  const [sources, setSources] = useState([]);
  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [loading, setLoading] = useState(false);

  // Fetch sources for selector on mount
  useEffect(() => {
    fetch("/sources")
      .then(r => r.json())
      .then(data => {
        const list = Array.isArray(data) ? data : (data.sources || []);
        setSources(list);
        if (list.length > 0 && !selectedSourceId) {
          setSelectedSourceId(list[0].source_id);
        }
      })
      .catch(() => {});
  }, []);

  const currentResults = validationResults[selectedSourceId] || null;

  const runValidations = useCallback(() => {
    if (!selectedSourceId) return;
    setLoading(true);
    fetch(`/pipelines/${selectedSourceId}/run?run_type=validations`, { method: "POST" })
      .then(r => {
        if (!r.ok) return r.json().then(e => Promise.reject(e));
        return r.json();
      })
      .then(data => {
        setValidationResults(prev => ({ ...prev, [selectedSourceId]: data.steps || [] }));
        flash("Validations complete", "ok");
      })
      .catch(err => {
        flash(err?.detail || "Validation run failed", "error");
      })
      .finally(() => setLoading(false));
  }, [selectedSourceId, flash, setValidationResults]);

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* Header */}
      <div style={{
        padding: "16px 24px", borderBottom: "1px solid var(--border)", flexShrink: 0,
      }}>
        <div style={{ fontWeight: 600, fontSize: 16 }}>Results</div>
        <div style={{ color: "var(--text-3)", fontSize: 12 }}>
          Run validations to see per-function pass/fail counts
        </div>
      </div>

      {/* Sub-tab bar */}
      <div style={{
        display: "flex", gap: 0, padding: "0 24px",
        borderBottom: "1px solid var(--border)", flexShrink: 0,
      }}>
        {[
          { id: "by-source", label: "By Source", disabled: false },
          { id: "by-function", label: "By Function", disabled: true },
        ].map(tab => (
          <button key={tab.id} disabled={tab.disabled} style={{
            padding: "10px 16px", fontSize: 13, fontWeight: 500,
            background: "transparent", border: "none",
            borderBottom: tab.id === "by-source" ? "2px solid var(--accent)" : "2px solid transparent",
            color: tab.disabled ? "var(--text-4)" : (tab.id === "by-source" ? "var(--accent)" : "var(--text-2)"),
            cursor: tab.disabled ? "not-allowed" : "pointer",
            opacity: tab.disabled ? 0.45 : 1,
          }}>
            {tab.label}
          </button>
        ))}
      </div>

      {/* Controls row */}
      <div style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: "12px 24px", borderBottom: "1px solid var(--border)", flexShrink: 0,
      }}>
        <select
          value={selectedSourceId}
          onChange={e => setSelectedSourceId(e.target.value)}
          style={{
            padding: "6px 10px", borderRadius: "var(--radius)",
            border: "1px solid var(--border)", background: "var(--panel-3)",
            color: "var(--text-1)", fontSize: 13, minWidth: 200,
          }}
        >
          {sources.length === 0 && (
            <option value="">No sources</option>
          )}
          {sources.map(s => (
            <option key={s.source_id} value={s.source_id}>
              {s.source_name || s.name || s.source_id}
            </option>
          ))}
        </select>

        <Btn
          variant="primary"
          onClick={runValidations}
          disabled={loading || !selectedSourceId}
        >
          {loading ? "Running…" : "Run Validations"}
        </Btn>
      </div>

      {/* Content area */}
      <div style={{ flex: 1, overflow: "auto" }}>
        {loading && (
          <div style={{
            flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
            color: "var(--text-4)", fontSize: 14, padding: 48,
          }}>
            Running validations…
          </div>
        )}
        {!loading && currentResults === null && (
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            color: "var(--text-4)", fontSize: 14, padding: 48,
          }}>
            Select a source and click "Run Validations" to see results.
          </div>
        )}
        {!loading && currentResults !== null && currentResults.length === 0 && (
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            color: "var(--text-4)", fontSize: 14, padding: 48,
          }}>
            No validation functions are attached to this source.
          </div>
        )}
        {!loading && currentResults !== null && currentResults.length > 0 && (
          <ValidationResultsTable
            steps={currentResults}
            sourceName={(sources.find(s => s.source_id === selectedSourceId) || {}).source_name || selectedSourceId}
          />
        )}
      </div>
    </div>
  );
}

window.__ScreenResults__ = ScreenResults;
