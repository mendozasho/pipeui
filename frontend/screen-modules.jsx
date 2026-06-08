// Functions screen — Phase D placeholder (mock data)
const { useState } = React;

function ScreenModules() {
  const { KindTag, Icon, Btn } = window.__UI__;
  const { MODULES } = window.__DATA__;
  const [selected, setSelected] = useState(null);

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div style={{
        padding: "16px 24px", borderBottom: "1px solid var(--border)",
        display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0,
      }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 16 }}>Functions</div>
          <div style={{ color: "var(--text-3)", fontSize: 12 }}>Upload .py modules — coming in Phase D</div>
        </div>
        <Btn icon="upload" disabled>Upload module</Btn>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: 24 }}>
        {MODULES.map(mod => (
          <div key={mod.id} style={{
            background: "var(--panel)", border: "1px solid var(--border)",
            borderRadius: "var(--radius-lg)", marginBottom: 12, overflow: "hidden",
          }}>
            <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border-soft)", display: "flex", alignItems: "center", gap: 8 }}>
              <Icon name="file" size={14} style={{ color: "var(--text-3)" }} />
              <span style={{ fontWeight: 600, fontFamily: "'Geist Mono', monospace", fontSize: 13 }}>{mod.name}</span>
              <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-3)" }}>{mod.functions.length} functions</span>
            </div>
            {mod.functions.map(fn => (
              <div key={fn.id} style={{
                padding: "12px 16px", display: "flex", alignItems: "flex-start", gap: 12,
                borderBottom: "1px solid var(--border-soft)", cursor: "pointer",
              }}
                onClick={() => setSelected(fn)}
              >
                <KindTag kind={fn.kind} />
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 500, marginBottom: 2 }}>{fn.name}</div>
                  <div style={{ fontSize: 12, color: "var(--text-3)" }}>{fn.doc}</div>
                  <div style={{ fontSize: 11, color: "var(--text-4)", fontFamily: "'Geist Mono', monospace", marginTop: 4 }}>{fn.sig}</div>
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

window.__ScreenModules__ = ScreenModules;
