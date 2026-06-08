// Report Builder screen — Phase E placeholder
function ScreenBuilder() {
  const { Icon } = window.__UI__;
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div style={{
        padding: "16px 24px", borderBottom: "1px solid var(--border)", flexShrink: 0,
      }}>
        <div style={{ fontWeight: 600, fontSize: 16 }}>Report Builder</div>
        <div style={{ color: "var(--text-3)", fontSize: 12 }}>Drag-and-drop pipeline builder — coming in Phase E</div>
      </div>
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-4)" }}>
        <div style={{ textAlign: "center" }}>
          <Icon name="builder" size={40} style={{ marginBottom: 12, opacity: 0.3 }} />
          <div style={{ fontSize: 14 }}>Coming in Phase E</div>
        </div>
      </div>
    </div>
  );
}

window.__ScreenBuilder__ = ScreenBuilder;
