// App shell — navigation rail, global state, flash notifications
const { useState, useCallback, useRef, useEffect } = React;

const NAV = [
  { id: "data",     label: "Data",      icon: "data" },
  { id: "modules",  label: "Functions", icon: "modules" },
  { id: "builder",  label: "Builder",   icon: "builder" },
  { id: "results",  label: "Results",   icon: "results" },
  { id: "settings", label: "Settings",  icon: "settings" },
];

function NavRail({ active, onChange }) {
  const { Icon } = window.__UI__;
  return (
    <nav style={{
      width: 64, display: "flex", flexDirection: "column", alignItems: "center",
      background: "var(--panel)", borderRight: "1px solid var(--border)",
      padding: "16px 0", gap: 4, flexShrink: 0,
    }}>
      <div style={{ marginBottom: 20, opacity: 0.8 }}>
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
          <rect x="3" y="3" width="8" height="8" rx="2" fill="var(--accent)" />
          <rect x="13" y="3" width="8" height="8" rx="2" fill="var(--accent)" opacity=".5" />
          <rect x="3" y="13" width="8" height="8" rx="2" fill="var(--accent)" opacity=".5" />
          <rect x="13" y="13" width="8" height="8" rx="2" fill="var(--accent)" />
        </svg>
      </div>

      {NAV.map(item => (
        <button key={item.id} title={item.label} onClick={() => onChange(item.id)} style={{
          width: 44, height: 44, borderRadius: "var(--radius)", cursor: "pointer",
          display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 3,
          background: active === item.id ? "var(--accent-soft)" : "transparent",
          border: active === item.id ? "1px solid var(--accent-line)" : "1px solid transparent",
          color: active === item.id ? "var(--accent)" : "var(--text-3)",
          transition: "all .15s",
        }}>
          <Icon name={item.icon} size={18} />
        </button>
      ))}
    </nav>
  );
}

let _flashId = 0;

function App() {
  const { Flash } = window.__UI__;
  const ScreenData = window.__ScreenData__;
  const ScreenModules = window.__ScreenModules__;
  const ScreenBuilder = window.__ScreenBuilder__;
  const ScreenResults = window.__ScreenResults__;
  const ScreenSettings = window.__ScreenSettings__;

  const [screen, setScreen] = useState("data");
  const [flashes, setFlashes] = useState([]);
  const [validationResults, setValidationResults] = useState({});

  const flash = useCallback((text, kind = "ok") => {
    const id = ++_flashId;
    setFlashes(f => [...f, { id, text, kind }]);
    setTimeout(() => setFlashes(f => f.filter(m => m.id !== id)), 4000);
  }, []);

  const dismissFlash = useCallback(id => setFlashes(f => f.filter(m => m.id !== id)), []);

  return (
    <>
      <NavRail active={screen} onChange={setScreen} />

      <main style={{ flex: 1, display: "flex", overflow: "hidden", position: "relative" }}>
        {screen === "data"     && <ScreenData flash={flash} />}
        {screen === "modules"  && <ScreenModules flash={flash} />}
        {screen === "builder"  && <ScreenBuilder flash={flash} onNavigate={setScreen} />}
        {screen === "results"  && <ScreenResults flash={flash} validationResults={validationResults} setValidationResults={setValidationResults} />}
        {screen === "settings" && <ScreenSettings flash={flash} />}
      </main>

      <Flash messages={flashes} onDismiss={dismissFlash} />
    </>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
