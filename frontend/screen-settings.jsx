const { useState, useEffect } = React;

const ACCENTS = [
  { label: "Violet",  value: "#7c6cf5" },
  { label: "Indigo",  value: "#6366f1" },
  { label: "Sky",     value: "#38bdf8" },
  { label: "Emerald", value: "#34d399" },
  { label: "Rose",    value: "#fb7185" },
];

function applyAccent(hex) {
  const r = document.documentElement;
  r.style.setProperty("--accent", hex);
  r.style.setProperty("--accent-soft", hex + "20");
  r.style.setProperty("--accent-line", hex + "66");
}

function applyDensity(d) {
  document.body.classList.remove("compact", "comfy");
  if (d !== "regular") document.body.classList.add(d);
}

function SectionHeader({ title }) {
  return (
    <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-3)", textTransform: "uppercase", letterSpacing: ".07em", marginBottom: 12 }}>
      {title}
    </div>
  );
}

function ScreenSettings({ flash }) {
  const { Btn } = window.__UI__;

  const [loaded, setLoaded] = useState(false);
  const [accent, setAccent] = useState("#7c6cf5");
  const [density, setDensity] = useState("regular");
  const [dbPath, setDbPath] = useState("pipeui.db");
  const [originalDbPath, setOriginalDbPath] = useState("pipeui.db");
  const [functionsPaths, setFunctionsPaths] = useState([]);
  const [newPathInput, setNewPathInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [restartBanner, setRestartBanner] = useState(false);

  useEffect(() => {
    fetch("/settings")
      .then(r => r.json())
      .then(data => {
        setAccent(data.accent);
        setDensity(data.density);
        setDbPath(data.db_path);
        setOriginalDbPath(data.db_path);
        setFunctionsPaths(data.functions_paths || []);
        applyAccent(data.accent);
        applyDensity(data.density);
        setLoaded(true);
      });
  }, []);

  function handleAccent(hex) {
    setAccent(hex);
    applyAccent(hex);
  }

  function handleDensity(d) {
    setDensity(d);
    applyDensity(d);
  }

  function handleAddPath() {
    const trimmed = newPathInput.trim();
    if (trimmed && !functionsPaths.includes(trimmed)) {
      setFunctionsPaths([...functionsPaths, trimmed]);
    }
    setNewPathInput("");
  }

  function handleRemovePath(path) {
    setFunctionsPaths(functionsPaths.filter(p => p !== path));
  }

  async function handleSave() {
    setSaving(true);
    const patch = {};
    if (accent !== undefined) patch.accent = accent;
    if (density !== undefined) patch.density = density;
    if (dbPath !== originalDbPath) patch.db_path = dbPath;
    patch.functions_paths = functionsPaths;

    try {
      const res = await fetch("/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      const data = await res.json();
      if (data.ok) {
        setOriginalDbPath(data.settings.db_path);
        if (data.restart_required) {
          setRestartBanner(true);
        } else {
          flash("Settings saved", "ok");
        }
      } else {
        flash("Failed to save settings", "err");
      }
    } catch {
      flash("Failed to save settings", "err");
    } finally {
      setSaving(false);
    }
  }

  if (!loaded) return (
    <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-3)" }}>
      Loading…
    </div>
  );

  return (
    <div style={{ flex: 1, overflowY: "auto", padding: "var(--sp-6)" }}>
      <div style={{ maxWidth: 480 }}>

        {restartBanner && (
          <div style={{
            marginBottom: "var(--sp-5)",
            padding: "10px 14px",
            background: "rgba(251,191,36,0.1)",
            border: "1px solid rgba(251,191,36,0.3)",
            borderRadius: "var(--radius)",
            color: "var(--warn)",
            fontSize: 13,
          }}>
            DB path changed — restart the server to apply.
          </div>
        )}

        <div style={{ marginBottom: "var(--sp-6)" }}>
          <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: "var(--sp-5)" }}>Settings</h2>
        </div>

        {/* Appearance */}
        <div style={{ marginBottom: "var(--sp-6)", paddingBottom: "var(--sp-6)", borderBottom: "1px solid var(--border)" }}>
          <SectionHeader title="Appearance" />

          <div style={{ marginBottom: "var(--sp-4)" }}>
            <div style={{ fontSize: 13, color: "var(--text-2)", marginBottom: 8 }}>Accent colour</div>
            <div style={{ display: "flex", gap: 8 }}>
              {ACCENTS.map(a => (
                <button key={a.value} title={a.label} onClick={() => handleAccent(a.value)} style={{
                  width: 26, height: 26, borderRadius: 99, background: a.value, cursor: "pointer",
                  border: accent === a.value ? "2px solid var(--text)" : "2px solid transparent",
                  flexShrink: 0,
                }} />
              ))}
            </div>
          </div>

          <div>
            <div style={{ fontSize: 13, color: "var(--text-2)", marginBottom: 8 }}>Density</div>
            <div style={{ display: "flex", gap: 6 }}>
              {["compact", "regular", "comfy"].map(d => (
                <button key={d} onClick={() => handleDensity(d)} style={{
                  flex: 1, padding: "6px 0", borderRadius: "var(--radius)", fontSize: 12, cursor: "pointer",
                  background: density === d ? "var(--accent)" : "var(--panel-3)",
                  color: density === d ? "var(--accent-ink)" : "var(--text-2)",
                  border: "1px solid " + (density === d ? "transparent" : "var(--border)"),
                  fontWeight: 500,
                }}>
                  {d[0].toUpperCase() + d.slice(1)}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* App */}
        <div style={{ marginBottom: "var(--sp-6)", paddingBottom: "var(--sp-6)", borderBottom: "1px solid var(--border)" }}>
          <SectionHeader title="App" />

          <div>
            <div style={{ fontSize: 13, color: "var(--text-2)", marginBottom: 8 }}>Database path</div>
            <input
              value={dbPath}
              onChange={e => setDbPath(e.target.value)}
              style={{
                width: "100%", padding: "7px 10px",
                background: "var(--panel-3)", border: "1px solid var(--border)",
                borderRadius: "var(--radius)", color: "var(--text)", fontSize: 13,
              }}
            />
            <div style={{ fontSize: 11, color: "var(--text-3)", marginTop: 5 }}>
              Relative to the server's working directory. Requires restart to take effect.
            </div>
          </div>
        </div>

        {/* Functions */}
        <div style={{ marginBottom: "var(--sp-6)" }}>
          <SectionHeader title="Functions" />

          <div style={{ marginBottom: "var(--sp-3)" }}>
            <div style={{ fontSize: 13, color: "var(--text-2)", marginBottom: 8 }}>Function scan paths</div>
            {functionsPaths.length === 0 && (
              <div style={{ fontSize: 12, color: "var(--text-4)", marginBottom: 8 }}>No paths configured.</div>
            )}
            {functionsPaths.map(p => (
              <div key={p} style={{
                display: "flex", alignItems: "center", justifyContent: "space-between",
                padding: "5px 10px", marginBottom: 4,
                background: "var(--panel-3)", border: "1px solid var(--border)",
                borderRadius: "var(--radius)", fontSize: 13,
              }}>
                <span style={{ color: "var(--text)", fontFamily: "var(--font-mono)", fontSize: 12 }}>{p}</span>
                <button onClick={() => handleRemovePath(p)} style={{
                  background: "none", border: "none", cursor: "pointer",
                  color: "var(--text-3)", fontSize: 14, lineHeight: 1, padding: "0 2px",
                }} title="Remove path">
                  &times;
                </button>
              </div>
            ))}
          </div>

          <div style={{ display: "flex", gap: 6 }}>
            <input
              value={newPathInput}
              onChange={e => setNewPathInput(e.target.value)}
              onKeyDown={e => e.key === "Enter" && handleAddPath()}
              placeholder="/path/to/functions"
              style={{
                flex: 1, padding: "7px 10px",
                background: "var(--panel-3)", border: "1px solid var(--border)",
                borderRadius: "var(--radius)", color: "var(--text)", fontSize: 13,
              }}
            />
            <Btn variant="secondary" onClick={handleAddPath}>Add</Btn>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-3)", marginTop: 5 }}>
            Directories the app will scan for .py function modules.
          </div>
        </div>

        <Btn variant="primary" onClick={handleSave} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Btn>

      </div>
    </div>
  );
}

window.__ScreenSettings__ = ScreenSettings;
