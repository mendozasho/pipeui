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

function FunctionDrawer({ functionId, onClose, flash, onRun, running }) {
  const { Drawer, KindTag, Btn, Spinner, StatusPill, SourceBadge, Icon } = window.__UI__;
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

          {/* Identity block */}
          <div style={{
            background: "var(--panel-2)", border: "1px solid var(--border)",
            borderRadius: "var(--radius)", padding: "14px 16px",
            display: "flex", flexDirection: "column", gap: 12,
          }}>
            {/* Top row: KindTag + StatusPill left, function_class right */}
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <KindTag kind={fn.function_type} />
              <StatusPill status={fn.is_active ? "active" : "inactive"} />
              <span style={{
                marginLeft: "auto", fontSize: 11, color: "var(--text-4)",
                fontFamily: "'Geist Mono', monospace",
              }}>
                {fn.function_class}
              </span>
            </div>
            {/* Divider */}
            <div style={{ height: 1, background: "var(--border-soft)" }} />
            {/* Run action */}
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <Btn variant="primary" size="md" onClick={() => onRun && onRun(fn)}
                   disabled={!fn.is_active || !!running}
                   style={{ alignSelf: "flex-start" }}>
                {running
                  ? <><Spinner size={13} color="var(--accent-ink)" /> Running…</>
                  : "Run"}
              </Btn>
              {!fn.is_active && (
                <span style={{ fontSize: 11.5, color: "var(--text-3)" }}>
                  Inactive — source file missing on disk. Rescan to restore.
                </span>
              )}
            </div>
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
            <FnKV label="Return type"><span className="mono">{fn.function_return_type}</span></FnKV>
            <FnKV label="Function type"><span className="mono">{fn.function_type}</span></FnKV>
            <FnKV label="Function class"><span className="mono">{fn.function_class}</span></FnKV>
            <FnKV label="Source file">
              <span className="mono" style={{ fontSize: 12, wordBreak: "break-all" }}>
                {fn.module_path}
              </span>
            </FnKV>
          </FnSection>

          {/* Attached sources */}
          <FnSection title={`Attached to (${fn.attached_sources?.length ?? 0})`}>
            {(!fn.attached_sources || fn.attached_sources.length === 0) ? (
              <div style={{ color: "var(--text-3)", fontSize: 12, padding: "4px 0" }}>
                Not attached to any sources yet.
              </div>
            ) : (
              fn.attached_sources.map(s => (
                <div key={s.source_id} style={{
                  display: "flex", alignItems: "center", gap: 11,
                  padding: "8px 0", borderBottom: "1px solid var(--border-soft)",
                }}>
                  <SourceBadge name={s.source_name} style={{ width: 24, height: 24, fontSize: 10 }} />
                  <span style={{ fontSize: 13, color: "var(--text)", fontWeight: 500 }}>{s.source_name}</span>
                  <Icon name="file" size={13} style={{ color: "var(--text-4)", marginLeft: "auto" }} />
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
// SetsTab — create and list function sets
// ---------------------------------------------------------------------------

function SetsTab({ flash, allFunctions, addResultCard, onNavigate }) {
  const { Icon, Btn, Spinner, KindTag } = window.__UI__;
  const [sets, setSets] = useState([]);
  const [setsLoading, setSetsLoading] = useState(true);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingSetId, setEditingSetId] = useState(null); // null = create mode
  const [runningSets, setRunningSets] = useState({}); // set_id -> bool

  // Editor state
  const [setName, setSetName] = useState("");
  const [setDesc, setSetDesc] = useState("");
  const [members, setMembers] = useState([]); // [{function_id, function_name, function_type, is_active}]
  const [fnFilter, setFnFilter] = useState("");
  const [saving, setSaving] = useState(false);

  function loadSets() {
    return fetch("/function-sets")
      .then(r => r.json())
      .then(data => { setSets(data); setSetsLoading(false); })
      .catch(() => setSetsLoading(false));
  }

  useEffect(() => { loadSets(); }, []);

  function openCreate() {
    setEditingSetId(null);
    setSetName(""); setSetDesc(""); setMembers([]); setFnFilter(""); setEditorOpen(true);
  }

  function openEdit(setId) {
    fetch(`/function-sets/${setId}`)
      .then(r => r.json())
      .then(detail => {
        setEditingSetId(setId);
        setSetName(detail.set_name);
        setSetDesc(detail.set_description || "");
        // Map members to the shape the editor expects, merging with allFunctions for function_type
        setMembers(detail.members.map(m => ({
          function_id: m.function_id,
          function_name: m.function_name,
          function_type: m.function_type,
          is_active: m.is_active,
        })));
        setFnFilter("");
        setEditorOpen(true);
      })
      .catch(() => flash && flash("Could not load set detail.", "error"));
  }

  function addMember(fn) {
    if (members.some(m => m.function_id === fn.function_id)) return;
    setMembers(prev => [...prev, fn]);
  }

  function removeMember(id) {
    setMembers(prev => prev.filter(m => m.function_id !== id));
  }

  function moveMember(index, dir) {
    setMembers(prev => {
      const next = [...prev];
      const target = index + dir;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  function handleSave() {
    if (!setName.trim()) { flash && flash("Set name is required.", "error"); return; }
    setSaving(true);
    const isEdit = editingSetId !== null;
    const url = isEdit ? `/function-sets/${editingSetId}` : "/function-sets";
    const method = isEdit ? "PATCH" : "POST";
    fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        set_name: setName.trim(),
        set_description: setDesc.trim() || null,
        members: members.map(m => m.function_id),
      }),
    })
      .then(r => r.json())
      .then(data => {
        setSaving(false);
        if (!data.ok) {
          flash && flash(data.errors?.[0] || "Failed to save set.", "error");
          return;
        }
        setEditorOpen(false);
        setEditingSetId(null);
        loadSets();
        flash && flash(isEdit ? "Set updated." : "Set created.", "success");
      })
      .catch(() => { setSaving(false); flash && flash("Failed to save set.", "error"); });
  }

  const filteredFns = allFunctions.filter(fn =>
    fn.function_name.toLowerCase().includes(fnFilter.toLowerCase())
  );

  function handleRunSet(e, s) {
    e.stopPropagation();
    setRunningSets(prev => ({ ...prev, [s.set_id]: true }));
    fetch(`/pipelines/run-set?set_id=${s.set_id}`, { method: "POST" })
      .then(r => r.json().then(data => ({ ok: r.ok, data })))
      .then(({ ok, data }) => {
        setRunningSets(prev => ({ ...prev, [s.set_id]: false }));
        if (!ok) {
          flash && flash(data?.detail || "Run failed", "error");
          return;
        }
        // Aggregate summary across all sources and steps
        let rowsPassed = 0, rowsFailed = 0;
        for (const src of (data.sources || [])) {
          for (const step of (src.steps || [])) {
            rowsPassed += step.rows_passed || 0;
            rowsFailed += step.rows_failed || 0;
          }
        }
        const total = rowsPassed + rowsFailed;
        const passRate = total > 0 ? rowsPassed / total : null;
        const card = {
          run_id: crypto.randomUUID(),
          card_type: "validation",
          trigger: "function",
          source_id: null,
          source_name: null,
          function_id: null,
          function_name: null,
          set_id: data.set_id,
          set_name: data.set_name,
          run_at: new Date().toISOString(),
          summary: { rows_passed: rowsPassed, rows_failed: rowsFailed, pass_rate: passRate },
          sources: data.sources || [],
          steps: [],
        };
        addResultCard && addResultCard(card);
        onNavigate && onNavigate("results", {});
      })
      .catch(() => {
        setRunningSets(prev => ({ ...prev, [s.set_id]: false }));
        flash && flash("Run failed", "error");
      });
  }

  // ---- Editor view ----
  if (editorOpen) {
    return (
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {/* Editor header */}
        <div style={{
          padding: "16px 24px", borderBottom: "1px solid var(--border)",
          display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0,
        }}>
          <div style={{ fontWeight: 600, fontSize: 16 }}>{editingSetId ? "Edit Set" : "New Set"}</div>
          <div style={{ display: "flex", gap: 8 }}>
            {editingSetId && (
              <Btn onClick={() => {
                fetch(`/function-sets/${editingSetId}`, { method: "DELETE" })
                  .then(r => {
                    if (r.status === 204) {
                      setEditorOpen(false); setEditingSetId(null);
                      loadSets();
                      flash && flash("Set deleted.", "success");
                    } else {
                      flash && flash("Failed to delete set.", "error");
                    }
                  })
                  .catch(() => flash && flash("Failed to delete set.", "error"));
              }}>
                Delete
              </Btn>
            )}
            <Btn onClick={() => { setEditorOpen(false); setEditingSetId(null); }}>Cancel</Btn>
            <Btn icon="check" onClick={handleSave} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </Btn>
          </div>
        </div>

        {/* Name + description */}
        <div style={{ padding: "16px 24px", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
          <input
            value={setName}
            onChange={e => setSetName(e.target.value)}
            placeholder="Set name (required)"
            style={{
              width: "100%", boxSizing: "border-box",
              padding: "8px 12px", borderRadius: "var(--radius)",
              border: "1px solid var(--border)", background: "var(--panel-2)",
              color: "var(--text)", fontSize: 14, marginBottom: 8,
            }}
          />
          <input
            value={setDesc}
            onChange={e => setSetDesc(e.target.value)}
            placeholder="Description (optional)"
            style={{
              width: "100%", boxSizing: "border-box",
              padding: "8px 12px", borderRadius: "var(--radius)",
              border: "1px solid var(--border)", background: "var(--panel-2)",
              color: "var(--text)", fontSize: 13,
            }}
          />
        </div>

        {/* Two-panel body */}
        <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
          {/* Left: function picker */}
          <div style={{
            width: "50%", borderRight: "1px solid var(--border)",
            display: "flex", flexDirection: "column", overflow: "hidden",
          }}>
            <div style={{ padding: "10px 16px", borderBottom: "1px solid var(--border-soft)", flexShrink: 0 }}>
              <input
                value={fnFilter}
                onChange={e => setFnFilter(e.target.value)}
                placeholder="Filter functions…"
                style={{
                  width: "100%", boxSizing: "border-box",
                  padding: "6px 10px", borderRadius: "var(--radius)",
                  border: "1px solid var(--border)", background: "var(--panel-2)",
                  color: "var(--text)", fontSize: 12,
                }}
              />
            </div>
            <div style={{ flex: 1, overflow: "auto" }}>
              {filteredFns.length === 0 && (
                <div style={{ padding: 16, color: "var(--text-3)", fontSize: 12 }}>No functions.</div>
              )}
              {filteredFns.map(fn => {
                const already = members.some(m => m.function_id === fn.function_id);
                return (
                  <div
                    key={fn.function_id}
                    onClick={() => addMember(fn)}
                    style={{
                      padding: "10px 16px", borderBottom: "1px solid var(--border-soft)",
                      display: "flex", alignItems: "center", gap: 10,
                      cursor: already ? "default" : "pointer",
                      opacity: fn.is_active ? (already ? 0.4 : 1) : 0.4,
                      background: already ? "var(--panel-2)" : undefined,
                    }}
                  >
                    <KindTag kind={fn.function_type} />
                    <span style={{ fontSize: 13, flex: 1 }}>{fn.function_name}</span>
                    {already && <span style={{ fontSize: 11, color: "var(--text-3)" }}>added</span>}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Right: ordered members */}
          <div style={{ width: "50%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <div style={{
              padding: "10px 16px", borderBottom: "1px solid var(--border-soft)",
              fontSize: 11, color: "var(--text-3)", fontWeight: 600,
              textTransform: "uppercase", letterSpacing: ".05em", flexShrink: 0,
            }}>
              Pipeline ({members.length} function{members.length !== 1 ? "s" : ""})
            </div>
            <div style={{ flex: 1, overflow: "auto" }}>
              {members.length === 0 && (
                <div style={{ padding: 16, color: "var(--text-3)", fontSize: 12 }}>
                  Click functions on the left to add them.
                </div>
              )}
              {members.map((m, i) => (
                <div key={m.function_id} style={{
                  padding: "10px 16px", borderBottom: "1px solid var(--border-soft)",
                  display: "flex", alignItems: "center", gap: 8,
                  opacity: m.is_active ? 1 : 0.5,
                }}>
                  <span style={{ fontSize: 11, color: "var(--text-3)", minWidth: 20, textAlign: "right" }}>
                    {i + 1}
                  </span>
                  <KindTag kind={m.function_type} />
                  <span style={{ fontSize: 13, flex: 1 }}>{m.function_name}</span>
                  <div style={{ display: "flex", gap: 2 }}>
                    <button
                      onClick={() => moveMember(i, -1)} disabled={i === 0}
                      style={{
                        background: "none", border: "none", cursor: i === 0 ? "default" : "pointer",
                        color: "var(--text-3)", padding: "2px 4px", opacity: i === 0 ? 0.3 : 1,
                      }}
                      title="Move up"
                    >↑</button>
                    <button
                      onClick={() => moveMember(i, 1)} disabled={i === members.length - 1}
                      style={{
                        background: "none", border: "none",
                        cursor: i === members.length - 1 ? "default" : "pointer",
                        color: "var(--text-3)", padding: "2px 4px",
                        opacity: i === members.length - 1 ? 0.3 : 1,
                      }}
                      title="Move down"
                    >↓</button>
                    <button
                      onClick={() => removeMember(m.function_id)}
                      style={{
                        background: "none", border: "none", cursor: "pointer",
                        color: "var(--bad, #e85c5c)", padding: "2px 6px", fontSize: 14,
                      }}
                      title="Remove"
                    >×</button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ---- List view ----
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div style={{
        padding: "16px 24px", borderBottom: "1px solid var(--border)",
        display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0,
      }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 16 }}>Sets</div>
          <div style={{ color: "var(--text-3)", fontSize: 12 }}>
            {setsLoading ? "Loading…" : `${sets.length} set${sets.length !== 1 ? "s" : ""}`}
          </div>
        </div>
        <Btn icon="plus" onClick={openCreate}>New Set</Btn>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: 24 }}>
        {!setsLoading && sets.length === 0 && (
          <div style={{ color: "var(--text-3)", fontSize: 13 }}>
            No sets yet. Click "New Set" to create one.
          </div>
        )}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
          {sets.map(s => (
            <div key={s.set_id} onClick={() => openEdit(s.set_id)} style={{
              background: "var(--panel)", border: "1px solid var(--border)",
              borderRadius: "var(--radius-lg)", padding: "16px",
              cursor: "pointer",
            }}>
              <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 6 }}>
                <div style={{ fontWeight: 600, fontSize: 14 }}>{s.set_name}</div>
                {s.has_inactive && (
                  <span style={{
                    fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: 99,
                    background: "var(--warn-soft, rgba(232,160,32,.12))",
                    color: "var(--warn, #e8a020)",
                    border: "1px solid var(--warn, #e8a020)",
                    whiteSpace: "nowrap", marginLeft: 8,
                  }}>
                    ⚠ unavailable
                  </span>
                )}
              </div>
              {s.set_description && (
                <div style={{ fontSize: 12, color: "var(--text-3)", marginBottom: 8, lineHeight: 1.4 }}>
                  {s.set_description}
                </div>
              )}
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 6 }}>
                <div style={{ fontSize: 12, color: "var(--text-4)" }}>
                  {s.member_count} function{s.member_count !== 1 ? "s" : ""}
                </div>
                <Btn variant="primary" size="sm" onClick={e => handleRunSet(e, s)} disabled={!!runningSets[s.set_id]}>
                  {runningSets[s.set_id]
                    ? <><Spinner size={12} color="var(--accent-ink)" /> Running…</>
                    : "Run"}
                </Btn>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// ScreenModules
// ---------------------------------------------------------------------------

function ScreenModules({ flash, addResultCard, onNavigate }) {
  const { KindTag, Icon, Btn } = window.__UI__;
  const [activeTab, setActiveTab] = useState("functions");
  const [functions, setFunctions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [scanLog, setScanLog] = useState(null);
  const [scanOpen, setScanOpen] = useState(false);
  const [selectedFunction, setSelectedFunction] = useState(null);
  const [runningFns, setRunningFns] = useState({}); // function_id -> bool

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

  function handleRunFn(_e, fn) {
    setRunningFns(prev => ({ ...prev, [fn.function_id]: true }));
    fetch(`/validations/run?function_id=${fn.function_id}`, { method: "POST" })
      .then(r => r.json().then(data => ({ ok: r.ok, data })))
      .then(({ ok, data }) => {
        setRunningFns(prev => ({ ...prev, [fn.function_id]: false }));
        if (!ok) {
          flash && flash(data?.detail || "Run failed", "error");
          return;
        }
        // Aggregate summary across all sources
        let rowsPassed = 0, rowsFailed = 0;
        for (const src of (data.sources || [])) {
          rowsPassed += src.rows_passed || 0;
          rowsFailed += src.rows_failed || 0;
        }
        const total = rowsPassed + rowsFailed;
        const passRate = total > 0 ? rowsPassed / total : null;
        const card = {
          run_id: crypto.randomUUID(),
          card_type: "validation",
          trigger: "function",
          source_id: null,
          source_name: null,
          function_id: data.function_id,
          function_name: data.function_name,
          run_at: new Date().toISOString(),
          summary: { rows_passed: rowsPassed, rows_failed: rowsFailed, pass_rate: passRate },
          sources: data.sources || [],
          steps: [],
        };
        addResultCard && addResultCard(card);
        onNavigate && onNavigate("results", {});
      })
      .catch(() => {
        setRunningFns(prev => ({ ...prev, [fn.function_id]: false }));
        flash && flash("Run failed", "error");
      });
  }

  // Group functions by module_path for display
  const byFile = {};
  for (const fn of functions) {
    const key = fn.module_path || "(unknown)";
    if (!byFile[key]) byFile[key] = [];
    byFile[key].push(fn);
  }

  if (activeTab === "sets") {
    return (
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {/* Tab bar */}
        <div style={{
          padding: "0 24px", borderBottom: "1px solid var(--border)",
          display: "flex", gap: 0, flexShrink: 0,
        }}>
          {["functions", "sets"].map(tab => (
            <button key={tab} onClick={() => setActiveTab(tab)} style={{
              padding: "14px 18px", background: "none", border: "none",
              borderBottom: activeTab === tab ? "2px solid var(--accent)" : "2px solid transparent",
              color: activeTab === tab ? "var(--accent)" : "var(--text-3)",
              fontWeight: activeTab === tab ? 600 : 400,
              fontSize: 14, cursor: "pointer", textTransform: "capitalize",
              marginBottom: -1,
            }}>
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
            </button>
          ))}
        </div>
        <SetsTab flash={flash} allFunctions={functions} addResultCard={addResultCard} onNavigate={onNavigate} />
      </div>
    );
  }

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* Tab bar */}
      <div style={{
        padding: "0 24px", borderBottom: "1px solid var(--border)",
        display: "flex", gap: 0, flexShrink: 0,
      }}>
        {["functions", "sets"].map(tab => (
          <button key={tab} onClick={() => setActiveTab(tab)} style={{
            padding: "14px 18px", background: "none", border: "none",
            borderBottom: activeTab === tab ? "2px solid var(--accent)" : "2px solid transparent",
            color: activeTab === tab ? "var(--accent)" : "var(--text-3)",
            fontWeight: activeTab === tab ? 600 : 400,
            fontSize: 14, cursor: "pointer", textTransform: "capitalize",
            marginBottom: -1,
          }}>
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>
      {/* Functions tab header */}
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
            {/* Function cards — clickable to open drawer */}
            {fns.map(fn => (
              <div key={fn.function_id} onClick={() => setSelectedFunction(fn)} style={{
                padding: "12px 16px", display: "flex", alignItems: "flex-start", gap: 12,
                borderBottom: "1px solid var(--border-soft)",
                opacity: fn.is_active ? 1 : 0.5,
                cursor: "pointer",
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

      {/* Function detail drawer */}
      <FunctionDrawer
        functionId={selectedFunction?.function_id}
        onClose={() => setSelectedFunction(null)}
        flash={flash}
        onRun={fn => handleRunFn({ stopPropagation: () => {} }, fn)}
        running={selectedFunction ? !!runningFns[selectedFunction.function_id] : false}
      />
    </div>
  );
}

window.__ScreenModules__ = ScreenModules;
