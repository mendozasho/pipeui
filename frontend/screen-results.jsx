// Results screen — Phase E2 placeholder
function ScreenResults({ flash }) {
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div style={{
        padding: "16px 24px", borderBottom: "1px solid var(--border)", flexShrink: 0,
      }}>
        <div style={{ fontWeight: 600, fontSize: 16 }}>Results</div>
        <div style={{ color: "var(--text-3)", fontSize: 12 }}>
          Run a pipeline to see results here
        </div>
      </div>

      <div style={{
        flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
        color: "var(--text-4)", fontSize: 14,
      }}>
        Run a pipeline to see results here.
      </div>
    </div>
  );
}

window.__ScreenResults__ = ScreenResults;
