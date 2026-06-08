// Data screen — Phase A: dropzone → POST /sources, reports table → GET /sources
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

function SourceDrawer({ source, onClose }) {
  const { Drawer, StatusPill } = window.__UI__;
  if (!source) return null;
  return (
    <Drawer open={!!source} onClose={onClose} title={source.source_name} width={440}>
      <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        <Section title="Details">
          <KV label="Primary key">{source.primary_key}</KV>
          <KV label="Ingestion">{source.ingestion_method}</KV>
          <KV label="Registered">{source.date_registered}</KV>
          <KV label="Last ingested">{source.date_ingested || "—"}</KV>
          <KV label="Pattern">{source.pattern || "—"}</KV>
        </Section>

        <Section title={`Columns (${source.columns?.length ?? 0})`}>
          {(source.columns || []).map(col => (
            <div key={col.column_id} style={{
              display: "flex", justifyContent: "space-between", alignItems: "center",
              padding: "7px 0", borderBottom: "1px solid var(--border-soft)",
            }}>
              <span style={{ fontFamily: "'Geist Mono', monospace", fontSize: 12 }}>{col.column_name}</span>
              <span style={{
                fontSize: 11, padding: "2px 7px", borderRadius: 99,
                background: "var(--panel-3)", color: "var(--text-3)",
                fontFamily: "'Geist Mono', monospace",
              }}>{col.column_type}</span>
            </div>
          ))}
        </Section>
      </div>
    </Drawer>
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

function ScreenData({ flash }) {
  const { DataTable, SourceBadge, StatusPill, Icon } = window.__UI__;
  const [sources, setSources] = useState([]);
  const [loading, setLoading] = useState(true);
  const [pendingFile, setPendingFile] = useState(null);
  const [registering, setRegistering] = useState(false);
  const [selectedSource, setSelectedSource] = useState(null);

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
        flash(`"${data.source.source_name}" registered successfully.`, "ok");
        setPendingFile(null);
        await loadSources();
      } else {
        flash(data.errors?.join("; ") || "Registration failed.", "error");
      }
    } catch {
      flash("Network error during registration.", "error");
    } finally {
      setRegistering(false);
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
      key: "ingestion_method", label: "Method",
      render: v => <span style={{ fontSize: 12, color: "var(--text-3)" }}>{v}</span>,
    },
    {
      key: "primary_key", label: "PK",
      render: v => <span style={{ fontFamily: "'Geist Mono', monospace", fontSize: 12 }}>{v}</span>,
    },
    {
      key: "date_registered", label: "Registered",
      render: v => <span style={{ fontSize: 12, color: "var(--text-3)" }}>{v}</span>,
    },
    {
      key: "status", label: "Status",
      render: (_, row) => <StatusPill status={row.date_ingested ? "ingested" : "registered"} />,
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

        <div style={{
          background: "var(--panel)", border: "1px solid var(--border)",
          borderRadius: "var(--radius-lg)", overflow: "hidden",
        }}>
          <DataTable
            columns={columns}
            rows={sources.map(s => ({ ...s, id: s.source_id }))}
            onRowClick={row => setSelectedSource(row)}
            selectedId={selectedSource?.source_id}
          />
        </div>
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
      <SourceDrawer source={selectedSource} onClose={() => setSelectedSource(null)} />
    </div>
  );
}

window.__ScreenData__ = ScreenData;
