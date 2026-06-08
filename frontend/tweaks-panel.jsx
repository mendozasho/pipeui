// Dev-time tweaks panel — accent colour, density, nav layout
const { useState } = React;

const ACCENTS = [
  { label: "Violet",  value: "#7c6cf5" },
  { label: "Indigo",  value: "#6366f1" },
  { label: "Sky",     value: "#38bdf8" },
  { label: "Emerald", value: "#34d399" },
  { label: "Rose",    value: "#fb7185" },
];

function TweaksPanel({ open, onClose }) {
  const { Icon, Btn } = window.__UI__;

  const [accent, setAccent] = useState("#7c6cf5");
  const [density, setDensity] = useState("regular");

  function applyAccent(hex) {
    setAccent(hex);
    const r = document.documentElement;
    r.style.setProperty("--accent", hex);
    // derive soft/line from the hex
    r.style.setProperty("--accent-soft", hex + "20");
    r.style.setProperty("--accent-line", hex + "66");
  }

  function applyDensity(d) {
    setDensity(d);
    document.body.classList.remove("compact", "comfy");
    if (d !== "regular") document.body.classList.add(d);
  }

  if (!open) return null;

  return (
    <div style={{
      position: "fixed", bottom: 20, left: 20, zIndex: 200,
      background: "var(--panel-2)", border: "1px solid var(--border)",
      borderRadius: "var(--radius-lg)", padding: 16, width: 220,
      boxShadow: "0 8px 32px rgba(0,0,0,.5)",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
        <span style={{ fontWeight: 600, fontSize: 13 }}>Tweaks</span>
        <Btn variant="ghost" size="sm" icon="close" onClick={onClose} />
      </div>

      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 6, textTransform: "uppercase", letterSpacing: ".05em" }}>Accent</div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {ACCENTS.map(a => (
            <button key={a.value} title={a.label} onClick={() => applyAccent(a.value)} style={{
              width: 22, height: 22, borderRadius: 99, background: a.value, cursor: "pointer",
              border: accent === a.value ? "2px solid var(--text)" : "2px solid transparent",
            }} />
          ))}
        </div>
      </div>

      <div>
        <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 6, textTransform: "uppercase", letterSpacing: ".05em" }}>Density</div>
        <div style={{ display: "flex", gap: 4 }}>
          {["compact", "regular", "comfy"].map(d => (
            <button key={d} onClick={() => applyDensity(d)} style={{
              flex: 1, padding: "4px 0", borderRadius: "var(--radius)", fontSize: 11, cursor: "pointer",
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
  );
}

window.__TweaksPanel__ = TweaksPanel;
