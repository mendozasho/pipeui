// Results screen — F2-B: card grid + expand + CSV/xlsx export
// Each run appends a result card (most-recent-first).
// Validation cards expand to show per-function table + failing rows preview.
// Transform cards expand to fetch and show staging table preview.
// Cards have checkboxes; "Export Selected" bar appears when any are checked.
const { useState, useEffect, useRef } = React;

// ── Filename helpers ──────────────────────────────────────────────────────────
function sanitiseFilename(str) {
  return (str || "").replace(/[^a-zA-Z0-9_-]/g, "_");
}

function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

function exportFilename(sourceName, cardType, ext) {
  return `${sanitiseFilename(sourceName)}_${todayStr()}_${cardType}.${ext}`;
}

// ── Export format module ──────────────────────────────────────────────────────
function exportCsv(rows, filenameStem) {
  if (!rows || rows.length === 0) return;
  const cols = Object.keys(rows[0]);
  const lines = [cols.join(",")];
  for (const row of rows) {
    lines.push(cols.map(c => {
      const v = row[c];
      if (v === null || v === undefined) return "";
      const s = String(v);
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
  a.download = filenameStem + ".csv";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function exportXlsx(rows, filenameStem) {
  if (!rows || rows.length === 0) return;
  if (typeof XLSX === "undefined") {
    alert("SheetJS (XLSX) not loaded. Cannot export Excel file.");
    return;
  }
  const ws = XLSX.utils.json_to_sheet(rows);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Results");
  XLSX.writeFile(wb, filenameStem + ".xlsx");
}

const EXPORTERS = {
  csv: exportCsv,
  xlsx: exportXlsx,
};

// ── Type tag badge ────────────────────────────────────────────────────────────
function TypeTag({ cardType }) {
  const isValidation = cardType === "validation";
  return (
    <span style={{
      display: "inline-block", padding: "2px 8px", borderRadius: 99,
      background: isValidation ? "var(--check-bg, rgba(59,130,246,.12))" : "var(--xform-bg, rgba(245,158,11,.12))",
      color: isValidation ? "var(--check, #3b82f6)" : "var(--xform, #f59e0b)",
      fontSize: 11, fontWeight: 600, letterSpacing: ".03em",
    }}>
      {isValidation ? "validation" : "transform"}
    </span>
  );
}

// ── Inline format picker ──────────────────────────────────────────────────────
function InlineFormatPicker({ onExport }) {
  return (
    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
      <button
        onClick={e => { e.stopPropagation(); onExport("csv"); }}
        style={{
          padding: "3px 10px", fontSize: 11, fontWeight: 600,
          background: "var(--accent)", color: "var(--accent-ink, #fff)",
          border: "none", borderRadius: "var(--radius)", cursor: "pointer",
        }}
      >
        CSV
      </button>
      <button
        onClick={e => { e.stopPropagation(); onExport("xlsx"); }}
        style={{
          padding: "3px 10px", fontSize: 11, fontWeight: 600,
          background: "var(--panel-3)", color: "var(--text)",
          border: "1px solid var(--border)", borderRadius: "var(--radius)", cursor: "pointer",
        }}
      >
        Excel
      </button>
    </div>
  );
}

// ── Summary line ──────────────────────────────────────────────────────────────
function SummaryLine({ card }) {
  if (card.card_type === "validation") {
    const { rows_passed, rows_failed, pass_rate } = card.summary;
    const total = (rows_passed ?? 0) + (rows_failed ?? 0);
    return (
      <div style={{ display: "flex", gap: 16, fontSize: 12, color: "var(--text-2)" }}>
        <span>
          <span style={{ color: "var(--good)", fontWeight: 600 }}>{(rows_passed ?? 0).toLocaleString()}</span>
          {" passed"}
        </span>
        <span>
          <span style={{ color: rows_failed > 0 ? "var(--bad)" : "var(--text-3)", fontWeight: 600 }}>
            {(rows_failed ?? 0).toLocaleString()}
          </span>
          {" failed"}
        </span>
        {pass_rate !== null && pass_rate !== undefined && (
          <span style={{ color: "var(--text-3)" }}>
            {(pass_rate * 100).toFixed(1)}% pass rate
          </span>
        )}
        {total === 0 && (
          <span style={{ color: "var(--text-4)" }}>no counts</span>
        )}
      </div>
    );
  }
  if (card.card_type === "transform") {
    const { rows_affected } = card.summary;
    return (
      <div style={{ fontSize: 12, color: "var(--text-2)" }}>
        <span style={{ fontWeight: 600 }}>{(rows_affected ?? 0).toLocaleString()}</span>
        {" rows affected"}
      </div>
    );
  }
  return null;
}

// ── Validation badge ──────────────────────────────────────────────────────────
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

// ── Validation expand ─────────────────────────────────────────────────────────
const PREVIEW_CAP = 200;

function ValidationExpand({ card }) {
  const [expandedFn, setExpandedFn] = useState({});
  const steps = card.steps || [];

  const thStyle = {
    padding: "6px 12px", textAlign: "left", fontSize: 11, fontWeight: 600,
    color: "var(--text-3)", borderBottom: "1px solid var(--border)",
    whiteSpace: "nowrap",
  };
  const tdStyle = {
    padding: "8px 12px", fontSize: 13, borderBottom: "1px solid var(--border)",
    verticalAlign: "middle",
  };

  if (steps.length === 0) {
    return (
      <div style={{ color: "var(--text-4)", fontSize: 13 }}>No validation steps found.</div>
    );
  }

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
          {steps.map((step, i) => {
            const key = (step.function_id || step.set_name || "") + i;
            const hasFailingRows = step.failing_rows && step.failing_rows.length > 0;
            const isExpanded = !!expandedFn[key];
            const preview = hasFailingRows ? step.failing_rows.slice(0, PREVIEW_CAP) : [];
            const cols = preview.length > 0 ? Object.keys(preview[0]) : [];

            return (
              <React.Fragment key={key}>
                <tr
                  style={{ background: "var(--panel)", cursor: hasFailingRows ? "pointer" : "default" }}
                  onClick={() => {
                    if (hasFailingRows) setExpandedFn(prev => ({ ...prev, [key]: !prev[key] }));
                  }}
                >
                  <td style={{ ...tdStyle, width: 24, paddingRight: 0, color: "var(--text-4)", fontSize: 11 }}>
                    {hasFailingRows ? (isExpanded ? "▾" : "▸") : ""}
                  </td>
                  <td style={tdStyle}>
                    <span style={{ fontWeight: 500 }}>{step.function_name || step.set_name}</span>
                    {step.status === "failed" && step.error && (
                      <div style={{ color: "var(--bad)", fontSize: 11, marginTop: 3 }}>{step.error}</div>
                    )}
                  </td>
                  <td style={tdStyle}>
                    <ValidationBadge status={step.status || (step.rows_failed > 0 ? "fail" : "ok")} />
                  </td>
                  <td style={{ ...tdStyle, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                    {step.rows_passed !== null && step.rows_passed !== undefined ? step.rows_passed.toLocaleString() : "—"}
                  </td>
                  <td style={{ ...tdStyle, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                    {step.rows_failed !== null && step.rows_failed !== undefined ? step.rows_failed.toLocaleString() : "—"}
                  </td>
                  <td style={{ ...tdStyle, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                    {step.pass_rate !== null && step.pass_rate !== undefined
                      ? `${(step.pass_rate * 100).toFixed(1)}%`
                      : "—"}
                  </td>
                </tr>
                {isExpanded && hasFailingRows && (
                  <tr>
                    <td colSpan={6} style={{ padding: "0 0 0 32px", background: "var(--panel-2)" }}>
                      <div style={{ padding: "10px 12px 12px" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
                          <span style={{ fontSize: 12, color: "var(--text-3)" }}>
                            {step.failing_rows.length > PREVIEW_CAP
                              ? `Showing ${PREVIEW_CAP} of ${step.failing_rows.length.toLocaleString()} failing rows`
                              : `${step.failing_rows.length.toLocaleString()} failing row${step.failing_rows.length !== 1 ? "s" : ""}`}
                          </span>
                        </div>
                        <div style={{ overflowX: "auto", maxWidth: "100%" }}>
                          <table style={{ borderCollapse: "collapse", fontSize: 12 }}>
                            <thead>
                              <tr>
                                {cols.map(c => (
                                  <th key={c} style={{
                                    padding: "4px 10px", textAlign: "left", fontSize: 11, fontWeight: 600,
                                    color: "var(--text-3)", borderBottom: "1px solid var(--border)",
                                    whiteSpace: "nowrap", background: "var(--panel-2)",
                                  }}>{c}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {preview.map((r, ri) => (
                                <tr key={ri} style={{ background: ri % 2 === 0 ? "var(--panel)" : "var(--panel-2)" }}>
                                  {cols.map(c => (
                                    <td key={c} style={{
                                      padding: "4px 10px", fontSize: 12, borderBottom: "1px solid var(--border)",
                                      whiteSpace: "nowrap", color: "var(--text-2)",
                                    }}>
                                      {r[c] === null || r[c] === undefined
                                        ? <span style={{ color: "var(--text-4)" }}>null</span>
                                        : String(r[c])}
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
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Transform expand ──────────────────────────────────────────────────────────
function TransformExpand({ card }) {
  const [stagingData, setStagingData] = useState(null); // null=not loaded yet
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const fetchedRef = useRef(false);

  useEffect(() => {
    if (fetchedRef.current) return;
    fetchedRef.current = true;
    setLoading(true);
    fetch(`/pipelines/${card.source_id}/staging`)
      .then(r => {
        if (!r.ok) return r.json().then(e => Promise.reject(e));
        return r.json();
      })
      .then(data => setStagingData(data))
      .catch(err => setError(err?.detail || "Failed to load staging data"))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div style={{ color: "var(--text-4)", fontSize: 13 }}>Loading staging data…</div>;
  }
  if (error) {
    return <div style={{ color: "var(--bad)", fontSize: 13 }}>{error}</div>;
  }
  if (!stagingData || stagingData.rows.length === 0) {
    return <div style={{ color: "var(--text-4)", fontSize: 13 }}>No staging data available.</div>;
  }

  const { columns, rows } = stagingData;
  const preview = rows.slice(0, PREVIEW_CAP);

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
    <div>
      <div style={{ display: "flex", gap: 16, fontSize: 12, color: "var(--text-3)", marginBottom: 10 }}>
        <span><span style={{ color: "var(--text)", fontWeight: 600 }}>{rows.length.toLocaleString()}</span> rows</span>
        <span><span style={{ color: "var(--text)", fontWeight: 600 }}>{columns.length}</span> columns: {columns.join(", ")}</span>
      </div>
      <div style={{ overflowX: "auto", maxHeight: 320, overflowY: "auto" }}>
        <table style={{ borderCollapse: "collapse", fontSize: 12 }}>
          <thead style={{ position: "sticky", top: 0 }}>
            <tr>
              {columns.map(c => <th key={c} style={thStyle}>{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {preview.map((row, i) => (
              <tr key={i} style={{ background: i % 2 === 0 ? "var(--panel)" : "var(--panel-2)" }}>
                {columns.map(c => (
                  <td key={c} style={tdStyle}>
                    {row[c] === null || row[c] === undefined
                      ? <span style={{ color: "var(--text-4)" }}>null</span>
                      : String(row[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > PREVIEW_CAP && (
        <div style={{ fontSize: 11, color: "var(--text-4)", marginTop: 6 }}>
          Showing first {PREVIEW_CAP} of {rows.length.toLocaleString()} rows
        </div>
      )}
    </div>
  );
}

// ── Sources expand (function/set-scoped cards) ────────────────────────────────
function SourcesExpand({ sources }) {
  const [expandedSrc, setExpandedSrc] = useState({});
  const srcs = sources || [];

  const thStyle = {
    padding: "6px 12px", textAlign: "left", fontSize: 11, fontWeight: 600,
    color: "var(--text-3)", borderBottom: "1px solid var(--border)",
    whiteSpace: "nowrap",
  };
  const tdStyle = {
    padding: "8px 12px", fontSize: 13, borderBottom: "1px solid var(--border)",
    verticalAlign: "middle",
  };

  if (srcs.length === 0) {
    return <div style={{ color: "var(--text-4)", fontSize: 13 }}>No sources found.</div>;
  }

  return (
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
          {srcs.map((src, i) => {
            let rowsPassed = 0, rowsFailed = 0, allFailingRows = [];
            let anyFailed = false;
            for (const step of (src.steps || [])) {
              if (step.status === "failed") { anyFailed = true; continue; }
              rowsPassed += step.rows_passed || 0;
              rowsFailed += step.rows_failed || 0;
              if (step.failing_rows) allFailingRows.push(...step.failing_rows);
            }
            const srcStatus = src.error ? "failed" : (anyFailed ? "failed" : "ok");
            const total = rowsPassed + rowsFailed;
            const passRate = total > 0 ? rowsPassed / total : null;
            const hasFailingRows = allFailingRows.length > 0;
            const key = src.source_id || String(i);
            const isExpanded = !!expandedSrc[key];
            const preview = allFailingRows.slice(0, PREVIEW_CAP);
            const cols = preview.length > 0 ? Object.keys(preview[0]) : [];

            return (
              <React.Fragment key={key}>
                <tr
                  style={{ background: "var(--panel)", cursor: hasFailingRows ? "pointer" : "default" }}
                  onClick={() => {
                    if (hasFailingRows) setExpandedSrc(prev => ({ ...prev, [key]: !prev[key] }));
                  }}
                >
                  <td style={{ ...tdStyle, width: 24, paddingRight: 0, color: "var(--text-4)", fontSize: 11 }}>
                    {hasFailingRows ? (isExpanded ? "▾" : "▸") : ""}
                  </td>
                  <td style={tdStyle}>
                    <span style={{ fontWeight: 500 }}>{src.source_name}</span>
                    {src.error && (
                      <div style={{ color: "var(--bad)", fontSize: 11, marginTop: 3 }}>{src.error}</div>
                    )}
                  </td>
                  <td style={tdStyle}>
                    <ValidationBadge status={srcStatus} />
                  </td>
                  <td style={{ ...tdStyle, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                    {total > 0 ? rowsPassed.toLocaleString() : "—"}
                  </td>
                  <td style={{ ...tdStyle, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                    {total > 0 ? rowsFailed.toLocaleString() : "—"}
                  </td>
                  <td style={{ ...tdStyle, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                    {passRate !== null ? `${(passRate * 100).toFixed(1)}%` : "—"}
                  </td>
                </tr>
                {isExpanded && hasFailingRows && (
                  <tr>
                    <td colSpan={6} style={{ padding: "0 0 0 32px", background: "var(--panel-2)" }}>
                      <div style={{ padding: "10px 12px 12px" }}>
                        <div style={{ fontSize: 12, color: "var(--text-3)", marginBottom: 8 }}>
                          {allFailingRows.length > PREVIEW_CAP
                            ? `Showing ${PREVIEW_CAP} of ${allFailingRows.length.toLocaleString()} failing rows`
                            : `${allFailingRows.length.toLocaleString()} failing row${allFailingRows.length !== 1 ? "s" : ""}`}
                        </div>
                        <div style={{ overflowX: "auto", maxWidth: "100%" }}>
                          <table style={{ borderCollapse: "collapse", fontSize: 12 }}>
                            <thead>
                              <tr>
                                {cols.map(c => (
                                  <th key={c} style={{
                                    padding: "4px 10px", textAlign: "left", fontSize: 11, fontWeight: 600,
                                    color: "var(--text-3)", borderBottom: "1px solid var(--border)",
                                    whiteSpace: "nowrap", background: "var(--panel-2)",
                                  }}>{c}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {preview.map((r, ri) => (
                                <tr key={ri} style={{ background: ri % 2 === 0 ? "var(--panel)" : "var(--panel-2)" }}>
                                  {cols.map(c => (
                                    <td key={c} style={{
                                      padding: "4px 10px", fontSize: 12, borderBottom: "1px solid var(--border)",
                                      whiteSpace: "nowrap", color: "var(--text-2)",
                                    }}>
                                      {r[c] === null || r[c] === undefined
                                        ? <span style={{ color: "var(--text-4)" }}>null</span>
                                        : String(r[c])}
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
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Collect export rows for a card ────────────────────────────────────────────
function collectValidationExportRows(card) {
  // Function/set-scoped cards: aggregate failing_rows from per-source steps
  if (card.trigger === "function" && card.sources) {
    const allRows = [];
    for (const src of card.sources) {
      for (const step of (src.steps || [])) {
        if (step.failing_rows && step.failing_rows.length > 0) {
          allRows.push(...step.failing_rows);
        }
      }
    }
    return allRows;
  }
  const steps = card.steps || [];
  const allRows = [];
  for (const step of steps) {
    if (step.failing_rows && step.failing_rows.length > 0) {
      allRows.push(...step.failing_rows);
    }
  }
  return allRows;
}

// ── Single result card ────────────────────────────────────────────────────────
function ResultCard({ card, selected, onToggleSelect }) {
  const [expanded, setExpanded] = useState(false);
  const [showExportPicker, setShowExportPicker] = useState(false);
  const [stagingRowsCache, setStagingRowsCache] = useState(null);

  const ts = card.run_at ? new Date(card.run_at).toLocaleString() : "";
  const filenameStem = exportFilename(card.source_name, card.card_type, "");

  function handleExport(format) {
    setShowExportPicker(false);
    if (card.card_type === "validation") {
      const rows = collectValidationExportRows(card);
      EXPORTERS[format](rows, exportFilename(card.source_name, card.card_type, "").slice(0, -1));
    } else {
      // Transform: fetch staging if not cached
      if (stagingRowsCache) {
        EXPORTERS[format](stagingRowsCache, exportFilename(card.source_name, card.card_type, "").slice(0, -1));
      } else {
        fetch(`/pipelines/${card.source_id}/staging`)
          .then(r => r.json())
          .then(data => {
            setStagingRowsCache(data.rows);
            EXPORTERS[format](data.rows, exportFilename(card.source_name, card.card_type, "").slice(0, -1));
          })
          .catch(() => alert("Failed to fetch staging data for export."));
      }
    }
  }

  // Compute filename stems cleanly
  const stem = `${sanitiseFilename(card.source_name)}_${todayStr()}_${card.card_type}`;

  return (
    <div style={{
      background: "var(--panel)",
      border: selected
        ? "1.5px solid var(--accent)"
        : "1px solid var(--border)",
      borderRadius: "var(--radius-lg)",
      overflow: "hidden",
      boxShadow: selected ? "0 0 0 2px var(--accent-soft)" : "none",
    }}>
      {/* Card face */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "14px 18px",
      }}>
        {/* Checkbox */}
        <input
          type="checkbox"
          checked={selected}
          onChange={e => { e.stopPropagation(); onToggleSelect(card.run_id); }}
          style={{ width: 15, height: 15, accentColor: "var(--accent)", flexShrink: 0, cursor: "pointer" }}
          onClick={e => e.stopPropagation()}
        />

        {/* Chevron */}
        <button
          onClick={() => setExpanded(e => !e)}
          style={{
            background: "none", border: "none", cursor: "pointer",
            color: "var(--text-4)", fontSize: 13, padding: 0, flexShrink: 0,
            lineHeight: 1,
          }}
          title="Expand detail"
        >
          {expanded ? "▾" : "▸"}
        </button>

        {/* Source/function/set name */}
        <div style={{ fontWeight: 600, fontSize: 14, flex: 1, minWidth: 0 }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block" }}>
            {card.trigger === "function"
              ? (card.function_name || card.set_name)
              : card.source_name}
          </span>
        </div>

        {/* Type tag */}
        <TypeTag cardType={card.card_type} />

        {/* Timestamp */}
        <span style={{ fontSize: 11, color: "var(--text-4)", whiteSpace: "nowrap", flexShrink: 0 }}>
          {ts}
        </span>
      </div>

      {/* Summary line */}
      <div style={{ padding: "0 18px 0 56px" }}>
        <SummaryLine card={card} />
      </div>

      {/* Inline export button row */}
      <div style={{ padding: "10px 18px 14px 56px", display: "flex", alignItems: "center", gap: 8 }}>
        {showExportPicker ? (
          <>
            <span style={{ fontSize: 12, color: "var(--text-3)" }}>Export as:</span>
            <InlineFormatPicker onExport={handleExport} />
            <button
              onClick={() => setShowExportPicker(false)}
              style={{ fontSize: 11, background: "none", border: "none", color: "var(--text-4)", cursor: "pointer" }}
            >
              ✕
            </button>
          </>
        ) : (
          <button
            onClick={e => { e.stopPropagation(); setShowExportPicker(true); }}
            style={{
              padding: "3px 10px", fontSize: 11, fontWeight: 600,
              background: "var(--panel-3)", color: "var(--text-2)",
              border: "1px solid var(--border)", borderRadius: "var(--radius)", cursor: "pointer",
            }}
          >
            Export
          </button>
        )}
      </div>

      {/* Expand detail */}
      {expanded && (
        <div style={{
          borderTop: "1px solid var(--border)",
          padding: "14px 18px",
          background: "var(--panel-2)",
        }}>
          {card.trigger === "function" && card.sources
            ? <SourcesExpand sources={card.sources} />
            : card.card_type === "validation"
              ? <ValidationExpand card={card} />
              : <TransformExpand card={card} />
          }
        </div>
      )}
    </div>
  );
}

// ── Export Selected bar ───────────────────────────────────────────────────────
function ExportSelectedBar({ selectedIds, cards, onClear }) {
  const [format, setFormat] = useState("csv");

  function handleExport() {
    const selectedCards = cards.filter(c => selectedIds.has(c.run_id));
    for (const card of selectedCards) {
      const stem = `${sanitiseFilename(card.source_name)}_${todayStr()}_${card.card_type}`;
      if (card.card_type === "validation") {
        const rows = collectValidationExportRows(card);
        EXPORTERS[format](rows, stem);
      } else {
        fetch(`/pipelines/${card.source_id}/staging`)
          .then(r => r.json())
          .then(data => EXPORTERS[format](data.rows, stem))
          .catch(() => alert(`Failed to fetch staging data for ${card.source_name}.`));
      }
    }
  }

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 12,
      padding: "10px 24px",
      background: "var(--panel-3)",
      borderBottom: "1px solid var(--border)",
      flexShrink: 0,
    }}>
      <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text)" }}>
        {selectedIds.size} selected
      </span>
      <span style={{ fontSize: 12, color: "var(--text-3)" }}>Format:</span>
      <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--text-2)", cursor: "pointer" }}>
        <input
          type="radio" name="export-format" value="csv"
          checked={format === "csv"}
          onChange={() => setFormat("csv")}
          style={{ accentColor: "var(--accent)" }}
        />
        CSV
      </label>
      <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--text-2)", cursor: "pointer" }}>
        <input
          type="radio" name="export-format" value="xlsx"
          checked={format === "xlsx"}
          onChange={() => setFormat("xlsx")}
          style={{ accentColor: "var(--accent)" }}
        />
        Excel
      </label>
      <button
        onClick={handleExport}
        style={{
          padding: "5px 14px", fontSize: 12, fontWeight: 600,
          background: "var(--accent)", color: "var(--accent-ink, #fff)",
          border: "none", borderRadius: "var(--radius)", cursor: "pointer",
        }}
      >
        Export
      </button>
      <button
        onClick={onClear}
        style={{
          padding: "5px 10px", fontSize: 11,
          background: "none", color: "var(--text-4)",
          border: "1px solid var(--border)", borderRadius: "var(--radius)", cursor: "pointer",
        }}
      >
        Clear
      </button>
    </div>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────
function ScreenResults({ flash, resultCards, resultsContext }) {
  const [selectedIds, setSelectedIds] = useState(new Set());

  function handleToggleSelect(runId) {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(runId)) {
        next.delete(runId);
      } else {
        next.add(runId);
      }
      return next;
    });
  }

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* Header */}
      <div style={{
        padding: "16px 24px", borderBottom: "1px solid var(--border)", flexShrink: 0,
      }}>
        <div style={{ fontWeight: 600, fontSize: 16 }}>Results</div>
        <div style={{ color: "var(--text-3)", fontSize: 12 }}>
          {resultCards.length > 0
            ? `${resultCards.length} run${resultCards.length !== 1 ? "s" : ""} in this session`
            : "Run a pipeline to see results here"}
        </div>
      </div>

      {/* Export Selected bar — shown when any cards are selected */}
      {selectedIds.size > 0 && (
        <ExportSelectedBar
          selectedIds={selectedIds}
          cards={resultCards}
          onClear={() => setSelectedIds(new Set())}
        />
      )}

      {/* Card grid */}
      <div style={{ flex: 1, overflow: "auto", padding: 24 }}>
        {resultCards.length === 0 ? (
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            height: "100%", color: "var(--text-4)", fontSize: 14, textAlign: "center",
          }}>
            Run a pipeline from the Builder screen to see results here.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 860 }}>
            {resultCards.map(card => {
              const highlighted = resultsContext && resultsContext.source_id
                ? card.source_id === resultsContext.source_id &&
                  card === resultCards.find(c => c.source_id === resultsContext.source_id)
                : false;
              return (
                <ResultCard
                  key={card.run_id}
                  card={card}
                  highlighted={highlighted}
                  selected={selectedIds.has(card.run_id)}
                  onToggleSelect={handleToggleSelect}
                />
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

window.__ScreenResults__ = ScreenResults;
