// Results screen — Phase F1: By Source + By Function sub-tabs
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

// ── Results table grouped by set_name (By Source view) ───────────────────────
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

// ── Per-source result row (By Function view) ──────────────────────────────────
function SourceResultRow({ entry, functionName }) {
  const [expanded, setExpanded] = useState(false);
  const hasFailingRows = entry.failing_rows && entry.failing_rows.length > 0;

  const tdStyle = {
    padding: "8px 12px", fontSize: 13, borderBottom: "1px solid var(--border)",
    verticalAlign: "middle",
  };

  return (
    <React.Fragment>
      <tr
        style={{ background: "var(--panel)", cursor: hasFailingRows ? "pointer" : "default" }}
        onClick={() => { if (hasFailingRows) setExpanded(e => !e); }}
      >
        <td style={{ ...tdStyle, width: 24, paddingRight: 0, color: "var(--text-4)", fontSize: 11 }}>
          {hasFailingRows ? (expanded ? "▾" : "▸") : ""}
        </td>
        <td style={tdStyle}>
          <span style={{ fontWeight: 500 }}>{entry.source_name}</span>
          {entry.status === "failed" && entry.error && (
            <div style={{ color: "var(--bad)", fontSize: 11, marginTop: 3 }}>{entry.error}</div>
          )}
        </td>
        <td style={tdStyle}><ValidationBadge status={entry.status} /></td>
        <td style={{ ...tdStyle, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
          {entry.rows_passed !== null && entry.rows_passed !== undefined ? entry.rows_passed.toLocaleString() : "—"}
        </td>
        <td style={{ ...tdStyle, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
          {entry.rows_failed !== null && entry.rows_failed !== undefined ? entry.rows_failed.toLocaleString() : "—"}
        </td>
        <td style={{ ...tdStyle, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
          {entry.pass_rate !== null && entry.pass_rate !== undefined
            ? `${(entry.pass_rate * 100).toFixed(1)}%`
            : "—"}
        </td>
      </tr>
      {expanded && hasFailingRows && (
        <tr>
          <td colSpan={6} style={{ padding: "0 0 0 32px", background: "var(--panel-2)" }}>
            <div style={{ padding: "10px 12px 12px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
                <span style={{ fontSize: 12, color: "var(--text-3)" }}>
                  {entry.failing_rows.length > PREVIEW_CAP
                    ? `Showing ${PREVIEW_CAP} of ${entry.failing_rows.length.toLocaleString()} failing rows`
                    : `${entry.failing_rows.length.toLocaleString()} failing row${entry.failing_rows.length !== 1 ? "s" : ""}`}
                </span>
                <button
                  onClick={e => { e.stopPropagation(); exportCsv(entry.failing_rows, entry.source_name, functionName); }}
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
                      {Object.keys(entry.failing_rows[0]).map(c => (
                        <th key={c} style={{
                          padding: "4px 10px", textAlign: "left", fontSize: 11, fontWeight: 600,
                          color: "var(--text-3)", borderBottom: "1px solid var(--border)",
                          whiteSpace: "nowrap", background: "var(--panel-2)",
                        }}>{c}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {entry.failing_rows.slice(0, PREVIEW_CAP).map((r, i) => (
                      <tr key={i} style={{ background: i % 2 === 0 ? "var(--panel)" : "var(--panel-2)" }}>
                        {Object.keys(entry.failing_rows[0]).map(c => (
                          <td key={c} style={{
                            padding: "4px 10px", fontSize: 12, borderBottom: "1px solid var(--border)",
                            whiteSpace: "nowrap", color: "var(--text-2)",
                          }}>
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
      )}
    </React.Fragment>
  );
}

// ── By Function view ──────────────────────────────────────────────────────────
function ByFunctionView({ flash, crossSourceResults, setCrossSourceResults }) {
  const { Btn } = window.__UI__;
  const [functions, setFunctions] = useState([]);
  const [selectedFunctionId, setSelectedFunctionId] = useState("");
  const [loading, setLoading] = useState(false);

  // Load validation functions on mount
  useEffect(() => {
    fetch("/functions")
      .then(r => r.json())
      .then(data => {
        const list = Array.isArray(data) ? data : (data.functions || []);
        const validations = list.filter(f => f.function_type === "validation");
        setFunctions(validations);
        if (validations.length > 0 && !selectedFunctionId) {
          setSelectedFunctionId(validations[0].function_id);
        }
      })
      .catch(() => {});
  }, []);

  const currentResults = selectedFunctionId ? (crossSourceResults[selectedFunctionId] || null) : null;

  const runAcrossSources = useCallback(() => {
    if (!selectedFunctionId) return;
    setLoading(true);
    fetch(`/validations/run?function_id=${selectedFunctionId}`, { method: "POST" })
      .then(r => {
        if (!r.ok) return r.json().then(e => Promise.reject(e));
        return r.json();
      })
      .then(data => {
        setCrossSourceResults(prev => ({ ...prev, [selectedFunctionId]: data }));
        flash("Cross-source validation complete", "ok");
      })
      .catch(err => {
        flash(err?.detail || "Validation run failed", "error");
      })
      .finally(() => setLoading(false));
  }, [selectedFunctionId, flash, setCrossSourceResults]);

  const thStyle = {
    padding: "6px 12px", textAlign: "left", fontSize: 11, fontWeight: 600,
    color: "var(--text-3)", borderBottom: "1px solid var(--border)",
    whiteSpace: "nowrap",
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, overflow: "hidden" }}>
      {/* Controls row */}
      <div style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: "12px 24px", borderBottom: "1px solid var(--border)", flexShrink: 0,
      }}>
        <select
          value={selectedFunctionId}
          onChange={e => setSelectedFunctionId(e.target.value)}
          style={{
            padding: "6px 10px", borderRadius: "var(--radius)",
            border: "1px solid var(--border)", background: "var(--panel-3)",
            color: "var(--text-1)", fontSize: 13, minWidth: 200,
          }}
        >
          {functions.length === 0 && (
            <option value="">No validation functions</option>
          )}
          {functions.map(f => (
            <option key={f.function_id} value={f.function_id}>
              {f.function_name}
            </option>
          ))}
        </select>

        <Btn
          variant="primary"
          onClick={runAcrossSources}
          disabled={loading || !selectedFunctionId}
        >
          {loading ? "Running…" : "Run Across All Sources"}
        </Btn>
      </div>

      {/* Content area */}
      <div style={{ flex: 1, overflow: "auto" }}>
        {loading && (
          <div style={{
            flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
            color: "var(--text-4)", fontSize: 14, padding: 48,
          }}>
            Running validations across all sources…
          </div>
        )}
        {!loading && currentResults === null && (
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            color: "var(--text-4)", fontSize: 14, padding: 48,
          }}>
            Select a validation function and click "Run Across All Sources" to see results.
          </div>
        )}
        {!loading && currentResults !== null && currentResults.sources.length === 0 && (
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            color: "var(--text-4)", fontSize: 14, padding: 48,
          }}>
            This function is not attached to any sources.
          </div>
        )}
        {!loading && currentResults !== null && currentResults.sources.length > 0 && (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={{ ...thStyle, width: 24 }}></th>
                  <th style={thStyle}>Source</th>
                  <th style={thStyle}>Status</th>
                  <th style={{ ...thStyle, textAlign: "right" }}>Passed</th>
                  <th style={{ ...thStyle, textAlign: "right" }}>Failed</th>
                  <th style={{ ...thStyle, textAlign: "right" }}>Pass rate</th>
                </tr>
              </thead>
              <tbody>
                {currentResults.sources.map(entry => (
                  <SourceResultRow
                    key={entry.source_id}
                    entry={entry}
                    functionName={currentResults.function_name}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────
function ScreenResults({
  flash,
  validationResults,
  setValidationResults,
  crossSourceResults,
  setCrossSourceResults,
  resultsContext,
}) {
  const { Btn } = window.__UI__;

  // Sub-tab: "by-source" | "by-function"
  const [activeTab, setActiveTab] = useState("by-source");

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

  // Apply deep-link context from Builder result-tag click
  useEffect(() => {
    if (resultsContext && resultsContext.source_id) {
      setActiveTab("by-source");
      setSelectedSourceId(resultsContext.source_id);
    }
  }, [resultsContext]);

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
          { id: "by-source", label: "By Source" },
          { id: "by-function", label: "By Function" },
        ].map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{
              padding: "10px 16px", fontSize: 13, fontWeight: 500,
              background: "transparent", border: "none",
              borderBottom: activeTab === tab.id ? "2px solid var(--accent)" : "2px solid transparent",
              color: activeTab === tab.id ? "var(--accent)" : "var(--text-2)",
              cursor: "pointer",
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* By Source tab content */}
      {activeTab === "by-source" && (
        <>
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
        </>
      )}

      {/* By Function tab content */}
      {activeTab === "by-function" && (
        <ByFunctionView
          flash={flash}
          crossSourceResults={crossSourceResults}
          setCrossSourceResults={setCrossSourceResults}
        />
      )}
    </div>
  );
}

window.__ScreenResults__ = ScreenResults;
