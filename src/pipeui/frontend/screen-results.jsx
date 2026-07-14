// Results screen — F2-B: card grid + expand + CSV/xlsx export
// Each run appends a result card (most-recent-first).
// Validation cards expand to show per-function table + failing rows preview.
// Transform cards expand to fetch and show staging table preview.
// Cards have checkboxes; "Export Selected" bar appears when any are checked.
const { useState, useEffect, useRef } = React;
const { LoadingState, InlineError, Icon, Btn, Drawer } = window.__UI__;

function timeAgo(iso) {
  const s = Math.round((Date.now() - new Date(iso)) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

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

// ── Fetch helper ──────────────────────────────────────────────────────────────
// Wraps fetch so callers get a real Error carrying the HTTP status and the
// FastAPI {detail} body instead of silently parsing an error payload (#110).
async function fetchJson(url, opts) {
  let r;
  try {
    r = await fetch(url, opts);
  } catch {
    throw new Error("Network error — is the server running?");
  }
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error((body && body.detail) || `Request failed (HTTP ${r.status})`);
  }
  return r.json();
}

// ── Export format module ──────────────────────────────────────────────────────
// Client-side exporters are used only for small in-memory row sets (validation
// summaries). Transform tables download server-side via exportTransform (#110).
// Each exporter returns an error string when nothing was exported, null on success.
function exportCsv(rows, filenameStem) {
  if (!rows || rows.length === 0) return "Nothing to export.";
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
  return null;
}

function exportXlsx(rows, filenameStem) {
  if (!rows || rows.length === 0) return "Nothing to export.";
  if (typeof XLSX === "undefined") {
    return "SheetJS (XLSX) not loaded — cannot export Excel file.";
  }
  const ws = XLSX.utils.json_to_sheet(rows);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Results");
  XLSX.writeFile(wb, filenameStem + ".xlsx");
  return null;
}

const EXPORTERS = {
  csv: exportCsv,
  xlsx: exportXlsx,
};

// ── Server-side transform download (#110) ─────────────────────────────────────
// Transform tables can reach GB scale — they must never round-trip through JSON.
// A bare anchor navigation lets the browser stream the file straight to disk;
// Content-Disposition: attachment keeps the SPA in place.

// xlsx sheet limit is 1,048,576 rows; one is reserved for the header.
const XLSX_EXPORT_MAX_ROWS = 1048575;

function triggerDownload(url) {
  const a = document.createElement("a");
  a.href = url;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

async function exportTransform(card, format, flash) {
  if (!card.source_id) {
    // Function/set-triggered cards span sources — there is no single transformed
    // table to download (#110 issue 4).
    flash("This run isn't tied to a single source — no transformed table to export.", "error");
    return;
  }
  // Cheap preflight: makes the anchor navigation safe (no raw JSON error page)
  // and catches the empty-table case with a message instead of a dead click.
  let meta;
  try {
    meta = await fetchJson(`/pipelines/${card.source_id}/staging/meta`);
  } catch (e) {
    flash(`Export failed: ${e.message}`, "error");
    return;
  }
  if (!meta.exists || meta.row_count === 0) {
    flash("Nothing to export — no transformed data for this source.", "error");
    return;
  }
  if (format === "xlsx" && meta.row_count > XLSX_EXPORT_MAX_ROWS) {
    flash(`Too many rows for Excel (${meta.row_count.toLocaleString()}). Export as CSV instead.`, "error");
    return;
  }
  triggerDownload(`/pipelines/${card.source_id}/export/transformed/file?format=${format}`);
}

// ── Card-type derivation (#193) ───────────────────────────────────────────────
// The card type is driven by the RunResult's function_type, so a mixed
// validation/transform set always renders the correct card variant per result.
// Falls back to an explicit card_type when present (back-compat with older cards).
function cardTypeForResult(result) {
  if (!result) return "validation";
  if (result.function_type === "transform" || result.function_type === "validation") {
    return result.function_type;
  }
  return result.card_type || "validation";
}

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

// #258: a failed run carries its error on the card's steps (pipeline path) or on its
// sources / nested steps (Functions/Set path). Surface it instead of a silent 0/0.
function cardFailureError(card) {
  const fromStep = (card.steps || []).find(s => s.status === "failed" && s.error);
  if (fromStep) return fromStep.error;
  for (const src of (card.sources || [])) {
    if (src.status === "failed" && src.error) return src.error;
    const st = (src.steps || []).find(s => s.status === "failed" && s.error);
    if (st) return st.error;
  }
  return null;
}

// ── Summary line ──────────────────────────────────────────────────────────────
function SummaryLine({ card, cardType }) {
  const resolved = cardType || cardTypeForResult(card);
  if (resolved === "validation") {
    const { rows_passed, rows_failed, pass_rate } = card.summary;
    const total = (rows_passed ?? 0) + (rows_failed ?? 0);
    // A failed run with no counts: show why it failed, not "0 passed / 0 failed".
    const failure = total === 0 ? cardFailureError(card) : null;
    if (failure) {
      return (
        <div style={{ fontSize: 12, color: "var(--bad)", fontWeight: 500 }}>
          {failure}
        </div>
      );
    }
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
  if (resolved === "transform") {
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
    if (!card.source_id) {
      // Function/set-triggered cards span sources — no single staging table (#110).
      setError("This run isn't tied to a single source.");
      return;
    }
    setLoading(true);
    fetchJson(`/pipelines/${card.source_id}/staging`)
      .then(data => setStagingData(data))
      .catch(err => setError(err.message || "Failed to load staging data"))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <LoadingState label="Loading staging data…" />;
  }
  if (error) {
    return <InlineError variant="panel">{error}</InlineError>;
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
// For function-triggered cards: one row per source with pass/fail counts.
function collectFunctionRunExportRows(card) {
  return (card.sources || []).map(src => {
    const passed = src.rows_passed ?? 0;
    const failed = src.rows_failed ?? 0;
    const total = passed + failed;
    return {
      source_name: src.source_name || src.source_id || "",
      rows_passed: passed,
      rows_failed: failed,
      pass_rate: total > 0 ? (passed / total * 100).toFixed(1) + "%" : null,
    };
  });
}

// One row per validation RESULT (function run) — the "final export" lists EVERY
// validation that ran, including passes AND crashes (#253). A crashed/failed run
// carries null counts plus its error so it still appears as a failure; a run that
// executed carries its pass/fail counts. The raw failing DATA records remain
// viewable in the card's expand-detail; this export is the per-function summary.
function collectValidationResultRows(card) {
  if (card.trigger === "function") {
    // Function-triggered: one row per source the function ran against.
    return collectFunctionRunExportRows(card);
  }
  return (card.steps || []).map(step => {
    const passed = step.rows_passed;
    const failed = step.rows_failed;
    const ran = passed !== null && passed !== undefined;
    const total = (passed ?? 0) + (failed ?? 0);
    return {
      function_name: step.function_name || step.set_name || "",
      label: step.label ?? "",
      // status reflects whether the function EXECUTED (ok) or crashed (failed),
      // not row-level pass/fail — a run with failing data rows is still "ok".
      status: step.status || (ran ? "ok" : "failed"),
      rows_passed: ran ? passed : null,
      rows_failed: ran ? failed : null,
      pass_rate: ran && total > 0 ? (passed / total * 100).toFixed(1) + "%" : null,
      error: step.error ?? null,
    };
  });
}

// ── Minimal results drawer body (slice 5 / #244) ──────────────────────────────
// Renders the RunResult metadata inside the EXISTING ui.jsx Drawer component
// (reuse, not a new drawer — the rich drawer is the design-gated slice 8). One
// labelled row per metadata field; missing fields are skipped.
function ResultMetaDrawerBody({ card }) {
  const resolvedType = cardTypeForResult(card);
  const fields = [
    ["Label", card.label],
    ["Function", card.function_name || card.set_name],
    ["Type", card.function_type || resolvedType],
    ["Status", card.status],
    ["Result ID", card.result_id],
  ];
  const summary = card.summary || {};
  if (summary.rows_passed !== undefined && summary.rows_passed !== null) {
    fields.push(["Passed", summary.rows_passed]);
  }
  if (summary.rows_failed !== undefined && summary.rows_failed !== null) {
    fields.push(["Failed", summary.rows_failed]);
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {fields
        .filter(([, v]) => v !== undefined && v !== null && v !== "")
        .map(([label, value]) => (
          <div key={label} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span style={{ fontSize: 11, color: "var(--text-3)", fontWeight: 600, letterSpacing: ".03em" }}>
              {label}
            </span>
            <span style={{ fontSize: 13, color: "var(--text)" }}>{String(value)}</span>
          </div>
        ))}
    </div>
  );
}

// ── Single result card ────────────────────────────────────────────────────────
function ResultCard({ card, selected, onToggleSelect, flash }) {
  const [expanded, setExpanded] = useState(false);
  const [showExportPicker, setShowExportPicker] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const ts = card.run_at ? timeAgo(card.run_at) : "";
  const tsAbsolute = card.run_at ? new Date(card.run_at).toISOString() : "";
  // Card type is driven by the RunResult function_type (#193): a mixed set renders
  // the correct variant per result, not a single set-wide tag.
  const resolvedCardType = cardTypeForResult(card);
  const filenameStem = exportFilename(card.source_name, resolvedCardType, "");

  function handleExport(format) {
    setShowExportPicker(false);
    if (resolvedCardType === "validation") {
      const label = sanitiseFilename(card.function_name || card.set_name || card.source_name);
      const exportRows = collectValidationResultRows(card);
      const err = EXPORTERS[format](exportRows, `${label}_${todayStr()}_validation`);
      if (err && flash) flash(err, "error");
    } else {
      // Transform: server-side file download — never fetch the rows (#110).
      exportTransform(card, format, flash || (() => {}));
    }
  }

  // Compute filename stems cleanly
  const stem = `${sanitiseFilename(card.source_name)}_${todayStr()}_${resolvedCardType}`;
  const isFunctionTriggered = card.trigger === "function";
  // Function-triggered cards always have source summary rows to export.
  // Source-triggered validation cards export once any validation ran (#253): the
  // export lists every result, so it is enabled whenever the card has steps.
  const hasExportRows = isFunctionTriggered
    ? (card.sources || []).length > 0
    : resolvedCardType === "validation"
      ? collectValidationResultRows(card).length > 0
      : !!card.source_id;
  const exportDisabled = !hasExportRows;
  const exportLabel = sanitiseFilename(card.function_name || card.set_name || card.source_name);
  const exportStem = resolvedCardType === "validation"
    ? `${exportLabel}_${todayStr()}_validation`
    : stem;

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

        {/* Source/function/set name + normalized RunResult label */}
        <div style={{ fontWeight: 600, fontSize: 14, flex: 1, minWidth: 0 }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block" }}>
            {card.trigger === "function"
              ? (card.function_name || card.set_name)
              : card.source_name}
          </span>
          {card.label && (
            <span style={{
              fontWeight: 500, fontSize: 12, color: "var(--text-3)",
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block",
            }}>
              {card.label}
            </span>
          )}
        </div>

        {/* Type tag — driven by RunResult function_type (#193) */}
        <TypeTag cardType={resolvedCardType} />

        {/* Timestamp */}
        <span
          title={tsAbsolute}
          style={{ fontSize: 11, color: "var(--text-4)", whiteSpace: "nowrap", flexShrink: 0, cursor: "default" }}
        >
          {ts}
        </span>
      </div>

      {/* Summary line */}
      <div style={{ padding: "0 18px 0 56px" }}>
        <SummaryLine card={card} cardType={resolvedCardType} />
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
          <>
            <button
              onClick={e => { e.stopPropagation(); if (!exportDisabled) setShowExportPicker(true); }}
              disabled={exportDisabled}
              style={{
                padding: "3px 10px", fontSize: 11, fontWeight: 600,
                background: exportDisabled ? "var(--panel-2)" : "var(--panel-3)",
                color: exportDisabled ? "var(--text-4)" : "var(--text-2)",
                border: "1px solid var(--border)", borderRadius: "var(--radius)",
                cursor: exportDisabled ? "not-allowed" : "pointer",
                opacity: exportDisabled ? 0.5 : 1,
              }}
              title={exportDisabled ? "No data to export" : undefined}
            >
              Export
            </button>
            {exportDisabled ? (
              <span style={{ fontSize: 11, color: "var(--text-4)" }}>No data to export</span>
            ) : (
              <span style={{ fontSize: 11, color: "var(--text-3)", fontFamily: "monospace" }}>
                {exportStem}.csv / .xlsx
              </span>
            )}
            {/* Minimal results drawer trigger (slice 5 / #244) */}
            <button
              onClick={e => { e.stopPropagation(); setDrawerOpen(true); }}
              style={{
                padding: "3px 10px", fontSize: 11, fontWeight: 600,
                background: "var(--panel-3)", color: "var(--text-2)",
                border: "1px solid var(--border)", borderRadius: "var(--radius)",
                cursor: "pointer", marginLeft: "auto",
              }}
              title="View run details"
            >
              Details
            </button>
          </>
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
            : resolvedCardType === "validation"
              ? <ValidationExpand card={card} />
              : <TransformExpand card={card} />
          }
        </div>
      )}

      {/* Minimal results drawer — reuses the existing ui.jsx Drawer (slice 5 / #244).
          The rich drawer is the design-gated slice 8; this shows RunResult metadata only. */}
      <Drawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={card.label || card.function_name || card.source_name || "Run detail"}
      >
        <ResultMetaDrawerBody card={card} />
      </Drawer>
    </div>
  );
}

// ── Export Selected bar ───────────────────────────────────────────────────────
function ExportSelectedBar({ selectedIds, cards, onClear, flash }) {
  const [format, setFormat] = useState("csv");

  function handleExport() {
    const selectedCards = cards.filter(c => selectedIds.has(c.run_id));
    for (const card of selectedCards) {
      // Card type derived per RunResult (#193) — matches the single-card path.
      if (cardTypeForResult(card) === "validation") {
        const bulkLabel = sanitiseFilename(card.function_name || card.set_name || card.source_name);
        // Same per-result summary as the single-card export (#253): every validation
        // listed, passes and crashes alike.
        const rows = collectValidationResultRows(card);
        const err = EXPORTERS[format](rows, `${bulkLabel}_${todayStr()}_validation`);
        if (err && flash) flash(`${bulkLabel}: ${err}`, "error");
      } else {
        // Server-side file download per card (#110). Chrome may prompt once to
        // allow multiple downloads when several cards are selected — acceptable.
        exportTransform(card, format, flash || (() => {}));
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

// ── Results empty state ───────────────────────────────────────────────────────
function ResultsEmptyState({ onNavigate }) {
  return (
    <div style={{
      display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center",
      height: "100%", textAlign: "center", padding: 24, gap: 18,
    }}>
      <div style={{
        width: 64, height: 64, borderRadius: "var(--radius-lg)",
        background: "var(--panel-2)", border: "1px solid var(--border)",
        display: "flex", alignItems: "center", justifyContent: "center",
        color: "var(--text-3)",
      }}>
        <Icon name="results" size={28} />
      </div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: "var(--text-2)" }}>
          No results yet
        </div>
        <div style={{ fontSize: 13, color: "var(--text-3)", maxWidth: 340,
          lineHeight: 1.6, textWrap: "balance" }}>
          Run a pipeline from the Builder screen to see
          validation and transform results here.
        </div>
      </div>
      {onNavigate && (
        <Btn variant="default" size="sm" icon="builder"
             onClick={() => onNavigate("builder")} style={{ marginTop: 2 }}>
          Go to Builder
        </Btn>
      )}
    </div>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────
function ScreenResults({ flash, resultCards, resultsContext, onNavigate }) {
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
          flash={flash}
        />
      )}

      {/* Card grid */}
      <div style={{ flex: 1, overflow: "auto", padding: 24 }}>
        {resultCards.length === 0 ? (
          <ResultsEmptyState onNavigate={onNavigate} />
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
                  flash={flash}
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

// Named exports for the dev-time vitest harness only. In the browser the file is
// loaded as a Babel "module", so these export statements are valid there too; the
// app consumes the screen via the window.__ScreenResults__ global above.
export {
  ScreenResults, ResultCard, SummaryLine, TypeTag, cardTypeForResult,
  collectValidationResultRows, fetchJson, exportCsv, exportXlsx,
  exportTransform, ExportSelectedBar, XLSX_EXPORT_MAX_ROWS,
};
