// Data screen — Phase B + B2: ingestion, drawer, data preview
const { useState, useRef, useEffect, useCallback } = React;

function DropZone({ onFiles }) {
  const { Icon, Btn } = window.__UI__;
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef(null);

  function handleDrop(e) {
    e.preventDefault();
    setDragging(false);
    const files = [...e.dataTransfer.files].filter(f => /\.(csv|xlsx)$/i.test(f.name));
    if (files.length) onFiles(files);
  }

  return (
    <div
      onDragOver={e => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      style={{
        border: `1.5px dashed ${dragging ? "var(--accent)" : "var(--border)"}`,
        borderRadius: "var(--radius-lg)",
        background: dragging ? "var(--accent-soft)" : "var(--panel)",
        padding: "40px 24px",
        textAlign: "center",
        cursor: "pointer",
        transition: "border-color .15s, background .15s",
      }}
      onClick={() => inputRef.current?.click()}
    >
      <Icon name="upload" size={28} style={{ color: "var(--text-3)", marginBottom: 10 }} />
      <div style={{ fontWeight: 600, marginBottom: 4 }}>Drop CSV or Excel files here</div>
      <div style={{ color: "var(--text-3)", fontSize: 12 }}>or click to browse</div>
      <input
        ref={inputRef}
        type="file"
        accept=".csv,.xlsx"
        multiple
        style={{ display: "none" }}
        onChange={e => { onFiles([...e.target.files]); e.target.value = ""; }}
      />
    </div>
  );
}

function RegisterModal({ file, onConfirm, onCancel }) {
  const { Btn, Icon } = window.__UI__;
  const [sourceName, setSourceName] = useState(file.name.replace(/\.[^.]+$/, ""));
  const [primaryKey, setPrimaryKey] = useState("");
  const [ingestionMethod, setIngestionMethod] = useState("upsert");

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 200,
      display: "flex", alignItems: "center", justifyContent: "center",
    }}>
      <div style={{
        background: "var(--panel)", border: "1px solid var(--border)",
        borderRadius: "var(--radius-lg)", padding: 24, width: 420,
        boxShadow: "0 16px 48px rgba(0,0,0,.6)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20 }}>
          <Icon name="file" size={18} style={{ color: "var(--text-3)" }} />
          <span style={{ fontWeight: 600, fontSize: 15 }}>Register source</span>
          <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--text-3)" }}>{file.name}</span>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <Field label="Source name">
            <input value={sourceName} onChange={e => setSourceName(e.target.value)}
              style={inputStyle} />
          </Field>
          <Field label="Primary key column">
            <input value={primaryKey} onChange={e => setPrimaryKey(e.target.value)}
              placeholder="e.g. id" style={inputStyle} />
          </Field>
          <Field label="Ingestion method">
            <select value={ingestionMethod} onChange={e => setIngestionMethod(e.target.value)}
              style={inputStyle}>
              <option value="upsert">Upsert</option>
              <option value="append">Append</option>
              <option value="skip">Skip duplicates</option>
            </select>
          </Field>
        </div>

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 24 }}>
          <Btn variant="ghost" onClick={onCancel}>Cancel</Btn>
          <Btn variant="primary" onClick={() => onConfirm({ sourceName, primaryKey, ingestionMethod })}
            disabled={!sourceName.trim() || !primaryKey.trim()}>
            Register
          </Btn>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 5 }}>
      <span style={{ fontSize: 12, color: "var(--text-2)", fontWeight: 500 }}>{label}</span>
      {children}
    </label>
  );
}

const inputStyle = {
  background: "var(--panel-2)", border: "1px solid var(--border)",
  borderRadius: "var(--radius)", padding: "7px 10px",
  color: "var(--text)", outline: "none", width: "100%",
};

function IngestModal({ source, onConfirm, onCancel }) {
  const { Btn, Icon } = window.__UI__;
  const [file, setFile] = useState(null);
  const inputRef = useRef(null);

  return (
    <div
      onClick={e => e.stopPropagation()}
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 300,
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: "var(--panel)", border: "1px solid var(--border)",
          borderRadius: "var(--radius-lg)", padding: 24, width: 400,
          boxShadow: "0 16px 48px rgba(0,0,0,.6)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20 }}>
          <Icon name="upload" size={18} style={{ color: "var(--text-3)" }} />
          <span style={{ fontWeight: 600, fontSize: 15 }}>Ingest into "{source.source_name}"</span>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
            <span style={{ fontSize: 12, color: "var(--text-2)", fontWeight: 500 }}>File (CSV or Excel)</span>
            <div
              onClick={() => inputRef.current?.click()}
              style={{
                ...inputStyle, cursor: "pointer",
                color: file ? "var(--text)" : "var(--text-3)",
              }}
            >
              {file ? file.name : "Click to choose file…"}
            </div>
            <input ref={inputRef} type="file" accept=".csv,.xlsx" style={{ display: "none" }}
              onChange={e => { if (e.target.files[0]) setFile(e.target.files[0]); e.target.value = ""; }} />
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 24 }}>
          <Btn variant="ghost" onClick={onCancel}>Cancel</Btn>
          <Btn variant="primary" onClick={() => onConfirm({ file })} disabled={!file}>
            Ingest
          </Btn>
        </div>
      </div>
    </div>
  );
}

const COLUMN_TYPES = ["INTEGER", "BIGINT", "DOUBLE", "BOOLEAN", "VARCHAR", "DATE", "TIMESTAMP"];

function MigrationConfirmModal({ uncastable, sharedSources, onConfirm, onCancel }) {
  const { Btn } = window.__UI__;
  const [scope, setScope] = React.useState("this_source");

  return (
    <div
      onClick={e => e.stopPropagation()}
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 400,
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: "var(--panel)", border: "1px solid var(--border)",
          borderRadius: "var(--radius-lg)", padding: 24, width: 420,
          boxShadow: "0 16px 48px rgba(0,0,0,.6)",
        }}
      >
        <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 16 }}>Confirm type migration</div>

        <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 20 }}>
          {uncastable > 0 && (
            <div style={{
              background: "var(--panel-2)", border: "1px solid var(--border)",
              borderRadius: "var(--radius)", padding: "10px 14px", fontSize: 13,
              color: "var(--text-2)",
            }}>
              <span style={{ fontWeight: 600, color: "var(--text)" }}>{uncastable}</span>
              {` value${uncastable !== 1 ? "s" : ""} will become NULL after migration.`}
            </div>
          )}

          {sharedSources.length > 0 && (
            <div style={{
              background: "var(--panel-2)", border: "1px solid var(--border)",
              borderRadius: "var(--radius)", padding: "10px 14px", fontSize: 13,
            }}>
              <div style={{ color: "var(--text-2)", marginBottom: 8 }}>This will also update:</div>
              <ul style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 4 }}>
                {sharedSources.map(name => (
                  <li key={name} style={{ fontFamily: "'Geist Mono', monospace", fontSize: 12, color: "var(--text-3)" }}>{name}</li>
                ))}
              </ul>
            </div>
          )}

          {sharedSources.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <span style={{ fontSize: 12, color: "var(--text-2)", fontWeight: 500 }}>Apply to</span>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {[
                  { value: "this_source", label: "This source only" },
                  { value: "all_shared", label: "All shared sources" },
                ].map(opt => (
                  <label key={opt.value} style={{
                    display: "flex", alignItems: "center", gap: 8,
                    fontSize: 13, cursor: "pointer", color: "var(--text)",
                  }}>
                    <input
                      type="radio"
                      name="migration-scope"
                      value={opt.value}
                      checked={scope === opt.value}
                      onChange={() => setScope(opt.value)}
                      style={{ accentColor: "var(--accent)", cursor: "pointer" }}
                    />
                    {opt.label}
                  </label>
                ))}
              </div>
            </div>
          )}
        </div>

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <Btn variant="ghost" onClick={onCancel}>Cancel</Btn>
          <Btn variant="primary" onClick={() => onConfirm(scope)}>Migrate anyway</Btn>
        </div>
      </div>
    </div>
  );
}

function ColumnTypeRow({ col, sourceId, onMigrated, onError }) {
  const [selected, setSelected] = React.useState(col.column_type);
  const [pendingMigration, setPendingMigration] = React.useState(null); // { newType, prev, uncastable, sharedSources }

  async function handleChange(e) {
    const newType = e.target.value;
    const prev = selected;
    setSelected(newType);

    try {
      // Step 1: dry-run
      const dryRes = await fetch(
        `/sources/${sourceId}/columns/${col.column_id}?dry_run=true`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ column_type: newType, scope: "this_source", on_uncastable: "abort" }),
        }
      );
      if (!dryRes.ok) { setSelected(prev); return; }
      const dryData = await dryRes.json();

      if (!dryData.ok) { setSelected(prev); return; }

      // Step 2: happy path — no uncastable rows and no shared sources
      if (dryData.uncastable === 0 && dryData.shared_sources.length === 0) {
        const commitRes = await fetch(
          `/sources/${sourceId}/columns/${col.column_id}`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ column_type: newType, scope: "this_source", on_uncastable: "nullify" }),
          }
        );
        if (!commitRes.ok) { setSelected(prev); return; }
        const commitData = await commitRes.json();
        if (!commitData.ok) { setSelected(prev); return; }
        await onMigrated(commitData.nullified || []);
      } else {
        // Show confirmation modal
        setPendingMigration({
          newType,
          prev,
          uncastable: dryData.uncastable,
          sharedSources: dryData.shared_sources,
        });
      }
    } catch (err) {
      console.error("Column type migration error:", err);
      setSelected(prev);
      onError && onError("Column type migration failed.");
    }
  }

  async function handleConfirmMigration(scope) {
    const { newType, prev } = pendingMigration;
    setPendingMigration(null);
    try {
      const commitRes = await fetch(
        `/sources/${sourceId}/columns/${col.column_id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ column_type: newType, scope, on_uncastable: "nullify" }),
        }
      );
      if (!commitRes.ok) { setSelected(prev); return; }
      const commitData = await commitRes.json();
      if (!commitData.ok) { setSelected(prev); return; }
      await onMigrated(commitData.nullified || []);
    } catch (err) {
      console.error("Column type migration commit error:", err);
      setSelected(prev);
      onError && onError("Column type migration failed.");
    }
  }

  function handleCancelMigration() {
    setSelected(pendingMigration.prev);
    setPendingMigration(null);
  }

  return (
    <>
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        padding: "7px 0", borderBottom: "1px solid var(--border-soft)",
      }}>
        <span style={{ fontFamily: "'Geist Mono', monospace", fontSize: 12 }}>{col.column_name}</span>
        <select
          value={selected}
          onChange={handleChange}
          style={{
            fontSize: 11, padding: "2px 7px", borderRadius: 99,
            background: "var(--panel-3)", color: "var(--text-3)",
            fontFamily: "'Geist Mono', monospace",
            border: "1px solid var(--border)", cursor: "pointer", outline: "none",
          }}
        >
          {COLUMN_TYPES.map(t => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
      </div>

      {pendingMigration && (
        <MigrationConfirmModal
          uncastable={pendingMigration.uncastable}
          sharedSources={pendingMigration.sharedSources}
          onConfirm={handleConfirmMigration}
          onCancel={handleCancelMigration}
        />
      )}
    </>
  );
}

function SourceDrawer({ sourceId, onClose, flash, onIngested }) {
  const { Drawer, StatusPill, Btn, Icon, Spinner, InlineError, LoadingState } = window.__UI__;
  const [detail, setDetail] = useState(null);
  const [showIngest, setShowIngest] = useState(false);
  const [ingesting, setIngesting] = useState(false);
  const [skipReport, setSkipReport] = useState(null);
  const [previewData, setPreviewData] = useState(null); // { columns, rows } | null
  const [nullifiedRows, setNullifiedRows] = useState([]); // [{pk, column}] after migration
  const [migrationError, setMigrationError] = useState(null);
  const [columnsExpanded, setColumnsExpanded] = useState(false);
  const [detailsExpanded, setDetailsExpanded] = useState(true);
  const [dataExpanded, setDataExpanded] = useState(false);

  async function loadDetail() {
    if (!sourceId) return;
    try {
      const res = await fetch(`/sources/${sourceId}`);
      if (res.ok) setDetail(await res.json());
    } catch {
      flash("Could not load source detail.", "error");
    }
  }

  async function loadRows(src) {
    if (!src?.date_ingested) { setPreviewData(null); return; }
    try {
      const res = await fetch(`/sources/${sourceId}/rows?limit=200`);
      if (res.ok) setPreviewData(await res.json());
      else setPreviewData({ columns: [], rows: [] });
    } catch {
      setPreviewData({ columns: [], rows: [] });
    }
  }

  useEffect(() => {
    setDetail(null);
    setPreviewData(null);
    setSkipReport(null);
    setNullifiedRows([]);
    setColumnsExpanded(false);
    setDetailsExpanded(true);
    setDataExpanded(false);
    if (!sourceId) return;
    (async () => {
      try {
        const res = await fetch(`/sources/${sourceId}`);
        if (res.ok) {
          const src = await res.json();
          setDetail(src);
          await loadRows(src);
        }
      } catch {
        flash("Could not load source detail.", "error");
      }
    })();
  }, [sourceId]);

  async function handleIngest({ file }) {
    setIngesting(true);
    setShowIngest(false);
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await fetch(`/sources/${sourceId}/ingest`, { method: "POST", body: fd });
      const data = await res.json();
      if (data.ok) {
        flash(`Ingested ${data.rows_ingested} row${data.rows_ingested !== 1 ? "s" : ""}.`, "ok");
        if (data.rows_skipped?.length) setSkipReport(data.rows_skipped);
        // Refresh detail then rows after a successful ingest
        try {
          const dres = await fetch(`/sources/${sourceId}`);
          if (dres.ok) {
            const src = await dres.json();
            setDetail(src);
            await loadRows(src);
          }
        } catch { /* ignore */ }
        onIngested();
      } else {
        flash(data.errors?.join("; ") || "Ingestion failed.", "error");
      }
    } catch {
      flash("Network error during ingestion.", "error");
    } finally {
      setIngesting(false);
    }
  }

  if (!sourceId) return null;
  const source = detail;

  return (
    <>
      <Drawer open={!!sourceId} onClose={onClose} title={source?.source_name ?? "…"} width={560}>
        {source && (
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <StatusPill status={source.date_ingested ? "ingested" : "registered"} />
              <span style={{ fontSize: 13, color: "var(--text-3)" }}>
                {source.row_count} row{source.row_count !== 1 ? "s" : ""}
              </span>
              {source.row_count != null && source.distinct_pk_count != null && source.row_count > source.distinct_pk_count && (
                <span style={{
                  display: "inline-flex", alignItems: "center", gap: 4,
                  background: "var(--warn-soft, rgba(255,180,0,.15))",
                  color: "var(--warn, #e6a000)",
                  border: "1px solid var(--warn, #e6a000)",
                  borderRadius: 99, padding: "2px 8px", fontSize: 11, fontWeight: 600,
                }}>
                  <Icon name="warn" size={12} />
                  Duplicate PK values
                </span>
              )}
              <Btn
                variant="ghost"
                style={{ marginLeft: "auto", fontSize: 12, display: "inline-flex", alignItems: "center", gap: 6 }}
                onClick={() => setShowIngest(true)}
                disabled={ingesting}
              >
                {ingesting ? <><Spinner size={12} /> Ingesting…</> : "Ingest file"}
              </Btn>
            </div>

            {skipReport && (
              <div style={{
                background: "var(--panel-2)", border: "1px solid var(--border)",
                borderRadius: "var(--radius)", padding: "10px 14px", fontSize: 12,
              }}>
                <div style={{ fontWeight: 600, marginBottom: 6, color: "var(--text-2)" }}>
                  {skipReport.length} row{skipReport.length !== 1 ? "s" : ""} skipped (duplicate key)
                </div>
                <div style={{ color: "var(--text-3)", fontFamily: "'Geist Mono', monospace", lineHeight: 1.7 }}>
                  {skipReport.join(", ")}
                </div>
              </div>
            )}

            <div>
              <div
                onClick={() => setDetailsExpanded(x => !x)}
                style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  cursor: "pointer", userSelect: "none", marginBottom: detailsExpanded ? 10 : 0,
                }}
              >
                <span style={{ fontSize: 11, color: "var(--text-3)", fontWeight: 600, letterSpacing: ".05em", textTransform: "uppercase" }}>Details</span>
                <span style={{ fontSize: 14, lineHeight: 1, color: "var(--text-3)" }}>{detailsExpanded ? "∨" : "›"}</span>
              </div>
              {detailsExpanded && (
                <>
                  <KV label="Primary key">{source.primary_key}</KV>
                  <KV label="Ingestion">{source.ingestion_method}</KV>
                  <KV label="Registered">{source.date_registered}</KV>
                  <KV label="Last ingested">{source.date_ingested || "—"}</KV>
                </>
              )}
            </div>

            <div>
              <div
                onClick={() => setColumnsExpanded(x => !x)}
                style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  cursor: "pointer", userSelect: "none", marginBottom: columnsExpanded ? 10 : 0,
                }}
              >
                <span style={{ fontSize: 11, color: "var(--text-3)", fontWeight: 600, letterSpacing: ".05em", textTransform: "uppercase" }}>Columns</span>
                <span style={{ fontSize: 12, color: "var(--text-3)", display: "flex", alignItems: "center", gap: 4 }}>
                  {source.columns?.length ?? 0}
                  <span style={{ fontSize: 14, lineHeight: 1 }}>{columnsExpanded ? " ∨" : " ›"}</span>
                </span>
              </div>
              {columnsExpanded && migrationError && (
                <InlineError variant="panel" onDismiss={() => setMigrationError(null)} style={{ marginBottom: 8 }}>
                  {migrationError}
                </InlineError>
              )}
              {columnsExpanded && (source.columns || []).map(col => (
                <ColumnTypeRow
                  key={col.column_id}
                  col={col}
                  sourceId={sourceId}
                  onError={setMigrationError}
                  onMigrated={async (nullified) => {
                    setMigrationError(null);
                    setNullifiedRows(nullified || []);
                    const dres = await fetch(`/sources/${sourceId}`);
                    if (dres.ok) {
                      const src = await dres.json();
                      setDetail(src);
                      await loadRows(src);
                    }
                  }}
                />
              ))}
            </div>

            {/* Nullified values — shown only after a migration that produced nullified rows */}
            {nullifiedRows.length > 0 && (
              <Section title={`Nullified values (${nullifiedRows.length})`}>
                {nullifiedRows.map((entry, i) => (
                  <div key={i} style={{
                    display: "flex", justifyContent: "space-between", alignItems: "center",
                    padding: "7px 0", borderBottom: "1px solid var(--border-soft)",
                  }}>
                    <span style={{ fontFamily: "'Geist Mono', monospace", fontSize: 12, color: "var(--text-3)" }}>
                      {entry.column}
                    </span>
                    <span style={{ fontFamily: "'Geist Mono', monospace", fontSize: 12, color: "var(--text)" }}>
                      {entry.pk}
                    </span>
                  </div>
                ))}
              </Section>
            )}

            {/* Data preview */}
            <div>
              <div
                onClick={() => setDataExpanded(x => !x)}
                style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  cursor: "pointer", userSelect: "none", marginBottom: dataExpanded ? 10 : 0,
                }}
              >
                <span style={{ fontSize: 11, color: "var(--text-3)", fontWeight: 600, letterSpacing: ".05em", textTransform: "uppercase" }}>Data</span>
                <span style={{ fontSize: 14, lineHeight: 1, color: "var(--text-3)" }}>{dataExpanded ? "∨" : "›"}</span>
              </div>
              {dataExpanded && (
                !source.date_ingested ? (
                  <div style={{ color: "var(--text-3)", fontSize: 12, padding: "8px 0" }}>
                    No data yet — ingest a file to preview rows.
                  </div>
                ) : !previewData ? (
                  <LoadingState />
                ) : previewData.rows.length === 0 ? (
                  <div style={{ color: "var(--text-3)", fontSize: 12, padding: "8px 0" }}>No rows in this source.</div>
                ) : (
                  <div style={{ overflowX: "auto", borderRadius: "var(--radius)", border: "1px solid var(--border)" }}>
                    <table style={{
                      borderCollapse: "collapse", width: "100%", fontSize: 12,
                      fontFamily: "'Geist Mono', monospace",
                    }}>
                      <thead>
                        <tr style={{ background: "var(--panel-2)" }}>
                          {previewData.columns.map(col => (
                            <th key={col} style={{
                              padding: "6px 10px", textAlign: "left", fontWeight: 600,
                              borderBottom: "1px solid var(--border)", color: "var(--text-2)",
                              whiteSpace: "nowrap",
                            }}>{col}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {previewData.rows.map((row, i) => (
                          <tr key={i} style={{ background: i % 2 === 0 ? "transparent" : "var(--panel-2)" }}>
                            {previewData.columns.map(col => (
                              <td key={col} style={{
                                padding: "5px 10px", borderBottom: "1px solid var(--border-soft)",
                                color: "var(--text)", whiteSpace: "nowrap", maxWidth: 200,
                                overflow: "hidden", textOverflow: "ellipsis",
                              }}>{row[col] == null ? <span style={{ color: "var(--text-4)" }}>null</span> : String(row[col])}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )
              )}
            </div>
          </div>
        )}
      </Drawer>

      {showIngest && source && (
        <IngestModal
          source={source}
          onConfirm={handleIngest}
          onCancel={() => setShowIngest(false)}
        />
      )}
    </>
  );
}

function Section({ title, children }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: "var(--text-3)", fontWeight: 600, letterSpacing: ".05em", textTransform: "uppercase", marginBottom: 10 }}>{title}</div>
      {children}
    </div>
  );
}

function KV({ label, children }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", borderBottom: "1px solid var(--border-soft)", fontSize: 13 }}>
      <span style={{ color: "var(--text-3)" }}>{label}</span>
      <span style={{ color: "var(--text)", fontWeight: 500 }}>{children}</span>
    </div>
  );
}

function ScreenData({ flash, addResultCard, onNavigate }) {
  const { DataTable, SourceBadge, StatusPill, Icon, Spinner, LoadingState } = window.__UI__;
  const [sources, setSources] = useState([]);
  const [loading, setLoading] = useState(true);
  const [pendingFile, setPendingFile] = useState(null);
  const [registering, setRegistering] = useState(false);
  const [selectedSource, setSelectedSource] = useState(null);
  const [runningSourceId, setRunningSourceId] = useState(null);

  async function loadSources() {
    try {
      const res = await fetch("/sources");
      if (res.ok) setSources(await res.json());
    } catch {
      flash("Could not reach the server.", "error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadSources(); }, []);

  async function handleRegister({ sourceName, primaryKey, ingestionMethod }) {
    if (!pendingFile) return;
    setRegistering(true);

    const fd = new FormData();
    fd.append("file", pendingFile);
    fd.append("source_name", sourceName);
    fd.append("primary_key", primaryKey);
    fd.append("ingestion_method", ingestionMethod);

    try {
      const res = await fetch("/sources", { method: "POST", body: fd });
      const data = await res.json();
      if (data.ok) {
        setPendingFile(null);
        await loadSources();
        if (data.matched_existing) {
          flash(`"${data.source.source_name}" matched an existing source — use Ingest to add data.`, "ok");
          setSelectedSource(data.source);
        } else {
          flash(`"${data.source.source_name}" registered successfully.`, "ok");
        }
      } else {
        flash(data.errors?.join("; ") || "Registration failed.", "error");
      }
    } catch {
      flash("Network error during registration.", "error");
    } finally {
      setRegistering(false);
    }
  }

  async function handleRunSource(source) {
    setRunningSourceId(source.source_id);
    try {
      const res = await fetch(`/pipelines/${source.source_id}/run?run_type=all`, { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        flash(err?.detail || "Run failed", "error");
        return;
      }
      const data = await res.json();
      const steps = data.steps || [];
      const validationSteps = steps.filter(s => s.function_type === "validation");
      const transformSteps = steps.filter(s => s.function_type === "transform");

      if (validationSteps.length > 0) {
        const passed = validationSteps.reduce((sum, s) => sum + (s.rows_passed ?? 0), 0);
        const failed = validationSteps.reduce((sum, s) => sum + (s.rows_failed ?? 0), 0);
        const total = passed + failed;
        addResultCard({
          run_id: crypto.randomUUID(),
          card_type: "validation",
          trigger: "source",
          source_id: source.source_id,
          source_name: source.source_name,
          run_at: new Date().toISOString(),
          summary: {
            rows_passed: passed,
            rows_failed: failed,
            pass_rate: total > 0 ? passed / total : null,
          },
          steps: validationSteps,
        });
      }

      if (transformSteps.length > 0) {
        const lastOk = [...transformSteps].reverse().find(s => s.status === "ok");
        addResultCard({
          run_id: crypto.randomUUID(),
          card_type: "transform",
          trigger: "source",
          source_id: source.source_id,
          source_name: source.source_name,
          run_at: new Date().toISOString(),
          summary: {
            rows_affected: lastOk?.rows_affected ?? 0,
            columns: [],
          },
          steps: transformSteps,
        });
      }

      onNavigate("results", { source_id: source.source_id });
    } catch (err) {
      flash("Run failed", "error");
    } finally {
      setRunningSourceId(null);
    }
  }

  const columns = [
    {
      key: "source_name", label: "Source",
      render: (v, row) => (
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <SourceBadge name={v} />
          <div>
            <div style={{ fontWeight: 500 }}>{v}</div>
            <div style={{ fontSize: 11, color: "var(--text-3)", fontFamily: "'Geist Mono', monospace" }}>
              {row.columns?.length ?? 0} columns
            </div>
          </div>
        </div>
      ),
    },
    {
      key: "row_count", label: "Rows",
      render: (v, row) => (
        <span style={{
          fontFamily: "'Geist Mono', monospace", fontSize: 12,
          color: row.date_ingested ? "var(--text)" : "var(--text-4)",
        }}>
          {row.date_ingested ? (v ?? 0).toLocaleString() : "—"}
        </span>
      ),
    },
    {
      key: "date_ingested", label: "Last ingested",
      render: v => (
        <span style={{ fontSize: 12, color: "var(--text-3)" }}>
          {v ? new Date(v).toLocaleDateString() : "—"}
        </span>
      ),
    },
    {
      key: "status", label: "Status",
      render: (_, row) => <StatusPill status={row.date_ingested ? "ingested" : "registered"} />,
    },
    {
      key: "_run", label: "",
      render: (_, row) => {
        const isRunning = runningSourceId === row.source_id;
        return (
          <button
            onClick={e => { e.stopPropagation(); handleRunSource(row); }}
            disabled={isRunning}
            style={{
              padding: "4px 12px", fontSize: 12, fontWeight: 600,
              background: isRunning ? "var(--panel-3)" : "var(--accent)",
              color: isRunning ? "var(--text-3)" : "#fff",
              border: "none", borderRadius: "var(--radius)", cursor: isRunning ? "default" : "pointer",
              whiteSpace: "nowrap",
              display: "inline-flex", alignItems: "center", gap: 6,
            }}
          >
            {isRunning ? <><Spinner size={12} /> Running…</> : "Run"}
          </button>
        );
      },
    },
  ];

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* Header */}
      <div style={{
        padding: "16px 24px", borderBottom: "1px solid var(--border)",
        display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0,
      }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 16 }}>Data</div>
          <div style={{ color: "var(--text-3)", fontSize: 12 }}>{sources.length} source{sources.length !== 1 ? "s" : ""} registered</div>
        </div>
      </div>

      {/* Body */}
      <div style={{ flex: 1, overflow: "auto", padding: 24, display: "flex", flexDirection: "column", gap: 20 }}>
        <DropZone onFiles={files => setPendingFile(files[0])} />

        {loading ? <LoadingState /> : (() => {
          // Partition sources into flat (no pattern) and grouped (by pattern_label).
          // If pattern_label is absent, derive it client-side from `pattern` by
          // replacing digit sequences with *, e.g. "sales_2024" → "sales_*".
          const grouped = {}, flat = [];
          for (const s of sources) {
            const label = s.pattern_label
              || (s.pattern ? s.pattern.replace(/\d+/g, "*") : null);
            if (label) {
              (grouped[label] ??= []).push({ ...s, _pattern_label: label });
            } else {
              flat.push(s);
            }
          }
          const groups = Object.entries(grouped).map(([label, rows]) => ({
            key: label,
            label,
            rows: rows.map(s => ({ ...s, id: s.source_id })),
            rowCount: rows.reduce((n, s) => n + (s.date_ingested ? (s.row_count || 0) : 0), 0),
          }));

          return (
            <div style={{
              background: "var(--panel)", border: "1px solid var(--border)",
              borderRadius: "var(--radius-lg)", overflow: "hidden",
            }}>
              <DataTable
                columns={columns}
                rows={flat.map(s => ({ ...s, id: s.source_id }))}
                groups={groups.length > 0 ? groups : undefined}
                onRowClick={row => setSelectedSource(row)}
                selectedId={selectedSource?.source_id}
              />
            </div>
          );
        })()}
      </div>

      {/* Register modal */}
      {pendingFile && !registering && (
        <RegisterModal
          file={pendingFile}
          onConfirm={handleRegister}
          onCancel={() => setPendingFile(null)}
        />
      )}

      {/* Source drawer */}
      <SourceDrawer
        sourceId={selectedSource?.source_id}
        onClose={() => setSelectedSource(null)}
        flash={flash}
        onIngested={loadSources}
      />
    </div>
  );
}

window.__ScreenData__ = ScreenData;
