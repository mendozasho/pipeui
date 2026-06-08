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

  async function handleSave() {
    setSaving(true);
    const patch = {};
    if (accent !== undefined) patch.accent = accent;
    if (density !== undefined) patch.density = density;
    if (dbPath !== originalDbPath) patch.db_path = dbPath;

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
        <div style={{ marginBottom: "var(--sp-6)" }}>
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

        <Btn variant="primary" onClick={handleSave} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Btn>

      </div>
    </div>
  );
}

window.__ScreenSettings__ = ScreenSettings;
