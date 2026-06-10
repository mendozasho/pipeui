// Shared UI primitives — Icon, Btn, KindTag, StatusPill, SourceBadge, DataTable
const { useState, useRef } = React;

// ── Icons (inline SVG, single source of truth) ───────────────────────────────
const ICONS = {
  data:     <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><ellipse cx="10" cy="5" rx="7" ry="2.5"/><path d="M3 5v5c0 1.38 3.13 2.5 7 2.5s7-1.12 7-2.5V5"/><path d="M3 10v5c0 1.38 3.13 2.5 7 2.5s7-1.12 7-2.5v-5"/></svg>,
  modules:  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="3" y="3" width="6" height="6" rx="1.5"/><rect x="11" y="3" width="6" height="6" rx="1.5"/><rect x="3" y="11" width="6" height="6" rx="1.5"/><rect x="11" y="11" width="6" height="6" rx="1.5"/></svg>,
  builder:  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M4 6h12M4 10h8M4 14h5"/><circle cx="15" cy="13" r="3"/><path d="m17.5 15.5 1.5 1.5"/></svg>,
  upload:   <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M10 13V4m0 0L7 7m3-3 3 3"/><path d="M3 14v1a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-1"/></svg>,
  close:    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="m5 5 10 10M15 5 5 15"/></svg>,
  check:    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2"><path d="m4 10 4.5 4.5L16 6"/></svg>,
  warn:     <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M10 3 2 17h16L10 3z"/><path d="M10 8v4M10 14v.5"/></svg>,
  chevron:  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="m7 8 3 3 3-3"/></svg>,
  file:     <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M5 3h7l4 4v10a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M12 3v4h4"/></svg>,
  eye:      <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><ellipse cx="10" cy="10" rx="7" ry="4.5"/><circle cx="10" cy="10" r="2"/></svg>,
  copy:     <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="7" y="7" width="9" height="9" rx="1.5"/><path d="M4 13V4a1 1 0 0 1 1-1h9"/></svg>,
  settings: <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="10" cy="10" r="2.5"/><path d="M10 2v1.5M10 16.5V18M2 10h1.5M16.5 10H18M4.22 4.22l1.06 1.06M14.72 14.72l1.06 1.06M4.22 15.78l1.06-1.06M14.72 5.28l1.06-1.06"/></svg>,
  drag:     <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="7.5" cy="6" r="1"/><circle cx="12.5" cy="6" r="1"/><circle cx="7.5" cy="10" r="1"/><circle cx="12.5" cy="10" r="1"/><circle cx="7.5" cy="14" r="1"/><circle cx="12.5" cy="14" r="1"/></svg>,
  plus:     <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M10 4v12M4 10h12"/></svg>,
  trash:    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M4 6h12l-1 11H5L4 6zM8 6V4h4v2M2 6h16"/></svg>,
  results:  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="3" y="3" width="14" height="14" rx="1.5"/><path d="M7 13V10M10 13V7M13 13v-2"/></svg>,
  group:    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="2" y="5" width="16" height="3" rx="1"/><rect x="4" y="10" width="12" height="2.5" rx="1" opacity=".6"/><rect x="6" y="14.5" width="8" height="2" rx="1" opacity=".35"/></svg>,
};

function Icon({ name, size = 16, style }) {
  const svg = ICONS[name];
  if (!svg) return null;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", width: size, height: size, flexShrink: 0, ...style }}>
      {React.cloneElement(svg, { width: size, height: size })}
    </span>
  );
}

// ── Btn ───────────────────────────────────────────────────────────────────────
function Btn({ children, variant = "default", size = "md", icon, onClick, disabled, style, type = "button" }) {
  const base = {
    display: "inline-flex", alignItems: "center", gap: 6, cursor: disabled ? "not-allowed" : "pointer",
    border: "1px solid", borderRadius: "var(--radius)", fontWeight: 500,
    transition: "opacity .15s", opacity: disabled ? 0.45 : 1, whiteSpace: "nowrap",
  };
  const sizes = {
    sm: { padding: "3px 10px", fontSize: 12 },
    md: { padding: "6px 14px", fontSize: 13 },
    lg: { padding: "9px 18px", fontSize: 14 },
  };
  const variants = {
    default: { background: "var(--panel-3)", borderColor: "var(--border)", color: "var(--text-2)" },
    primary: { background: "var(--accent)", borderColor: "transparent", color: "var(--accent-ink)" },
    ghost:   { background: "transparent", borderColor: "transparent", color: "var(--text-2)" },
    danger:  { background: "transparent", borderColor: "var(--bad)", color: "var(--bad)" },
  };
  return (
    <button type={type} style={{ ...base, ...sizes[size], ...variants[variant], ...style }} onClick={onClick} disabled={disabled}>
      {icon && <Icon name={icon} size={14} />}
      {children}
    </button>
  );
}

// ── KindTag (validation / transform / sql / unknown) ─────────────────────────
function KindTag({ kind }) {
  const isCheck   = kind === "validation";
  const isSql     = kind === "sql";
  const isUnknown = kind === "unknown";

  let bg, color, label, title;
  if (isCheck) {
    bg = "var(--check-bg)"; color = "var(--check)"; label = kind;
  } else if (isSql) {
    bg = "rgba(139,92,246,.15)"; color = "rgb(139,92,246)"; label = "SQL";
  } else if (isUnknown) {
    bg = "var(--panel-3)"; color = "var(--text-3)"; label = "Unknown";
    title = "Type unknown — add `-- type: transform` or `-- type: validation` to this file's header and rescan.";
  } else {
    bg = "var(--xform-bg)"; color = "var(--xform)"; label = kind;
  }

  return (
    <span title={title} style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      padding: "2px 8px", borderRadius: 99,
      background: bg, color,
      fontSize: 11, fontWeight: 600, letterSpacing: ".03em",
      cursor: isUnknown ? "help" : undefined,
    }}>
      {label}
    </span>
  );
}

// ── StatusPill ────────────────────────────────────────────────────────────────
function StatusPill({ status }) {
  const map = {
    registered: { color: "var(--text-3)", bg: "var(--panel-3)" },
    ingested:   { color: "var(--good)",   bg: "rgba(52,211,153,.1)" },
    error:      { color: "var(--bad)",    bg: "rgba(248,113,113,.1)" },
    running:    { color: "var(--run)",    bg: "rgba(96,165,250,.1)" },
    active:     { color: "var(--good)",   bg: "rgba(52,211,153,.1)" },
    inactive:   { color: "var(--text-3)", bg: "var(--panel-3)" },
  };
  const s = map[status] || map.registered;
  return (
    <span style={{
      display: "inline-block", padding: "2px 8px", borderRadius: 99,
      background: s.bg, color: s.color,
      fontSize: 11, fontWeight: 600, letterSpacing: ".03em",
    }}>
      {status}
    </span>
  );
}

// ── SourceBadge ───────────────────────────────────────────────────────────────
function SourceBadge({ name, style }) {
  const initials = name.slice(0, 2).toUpperCase();
  const hue = [...name].reduce((h, c) => h + c.charCodeAt(0), 0) % 360;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", justifyContent: "center",
      width: 28, height: 28, borderRadius: "var(--radius)",
      background: `hsl(${hue},50%,22%)`, color: `hsl(${hue},70%,70%)`,
      fontSize: 11, fontWeight: 700, flexShrink: 0, ...style,
    }}>
      {initials}
    </span>
  );
}

// ── Spinner ───────────────────────────────────────────────────────────────────
function Spinner({ size = 14, color = "currentColor", strokeWidth = 2 }) {
  const r = 8;
  const cx = 12, cy = 12;
  const circumference = 2 * Math.PI * r;
  const arcLength = circumference * 0.3;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" style={{ flexShrink: 0 }}>
      <circle cx={cx} cy={cy} r={r} stroke={color} strokeWidth={strokeWidth} strokeOpacity="0.22" />
      <circle cx={cx} cy={cy} r={r} stroke={color} strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeDasharray={`${arcLength} ${circumference - arcLength}`}
        strokeDashoffset="0"
      >
        <animateTransform
          attributeName="transform"
          type="rotate"
          from={`0 ${cx} ${cy}`}
          to={`360 ${cx} ${cy}`}
          dur="0.7s"
          repeatCount="indefinite"
        />
      </circle>
    </svg>
  );
}

// ── GroupHeader ───────────────────────────────────────────────────────────────
function GroupHeader({ name, count, rowCount, colSpan, collapsed, onToggle }) {
  return (
    <tr style={{ background: "var(--panel-2)" }}>
      <td colSpan={colSpan} style={{
        borderTop: "1px solid var(--border)",
        borderBottom: "1px solid var(--border-soft)",
        padding: "5px 12px",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {/* Left side */}
          <Icon name="group" size={14} style={{ color: "var(--text-3)", flexShrink: 0 }} />
          <span style={{
            fontFamily: "'Geist Mono', monospace", fontSize: 12,
            color: "var(--text-2)", fontWeight: 500,
          }}>
            {name}
          </span>
          <span style={{
            padding: "1px 7px", borderRadius: 99,
            background: "var(--panel-3)", border: "1px solid var(--border)",
            color: "var(--text-3)", fontSize: 11, fontWeight: 500,
          }}>
            {count} source{count !== 1 ? "s" : ""}
          </span>
          {/* Right side */}
          <span style={{
            marginLeft: "auto",
            fontFamily: "'Geist Mono', monospace", fontSize: 11,
            color: "var(--text-3)",
          }}>
            {rowCount.toLocaleString()} rows
          </span>
          <button
            onClick={onToggle}
            style={{
              background: "none", border: "none", cursor: "pointer",
              color: "var(--text-3)", display: "inline-flex", alignItems: "center",
              padding: 2,
            }}
          >
            <Icon name="chevron" size={14} style={{
              transform: collapsed ? "rotate(-90deg)" : "rotate(0deg)",
              transition: "transform .15s",
            }} />
          </button>
        </div>
      </td>
    </tr>
  );
}

// ── DataTable ─────────────────────────────────────────────────────────────────
function DataTable({ columns, rows, groups, onRowClick, selectedId }) {
  const [collapsedKeys, setCollapsedKeys] = useState(new Set());

  function toggleGroup(key) {
    setCollapsedKeys(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function renderRow(row, i) {
    return (
      <tr key={row.id ?? i}
        onClick={() => onRowClick && onRowClick(row)}
        style={{
          cursor: onRowClick ? "pointer" : "default",
          background: selectedId && row.id === selectedId ? "var(--hover)" : "transparent",
          borderBottom: "1px solid var(--border-soft)",
          transition: "background .1s",
        }}
        onMouseEnter={e => e.currentTarget.style.background = "var(--hover)"}
        onMouseLeave={e => e.currentTarget.style.background = (selectedId && row.id === selectedId) ? "var(--hover)" : "transparent"}
      >
        {columns.map((c, ci) => (
          <td key={c.key} style={{
            padding: "9px 12px",
            paddingLeft: ci === 0 && row.__grouped ? 34 : 12,
            color: "var(--text)", verticalAlign: "middle",
          }}>
            {c.render ? c.render(row[c.key], row) : row[c.key]}
          </td>
        ))}
      </tr>
    );
  }

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr>
            {columns.map(c => (
              <th key={c.key} style={{
                padding: "8px 12px", textAlign: "left",
                color: "var(--text-3)", fontWeight: 500, fontSize: 11, letterSpacing: ".05em",
                borderBottom: "1px solid var(--border)", whiteSpace: "nowrap",
              }}>
                {c.label.toUpperCase()}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {/* Flat (ungrouped) rows */}
          {rows.map((row, i) => renderRow(row, i))}

          {/* Grouped rows */}
          {groups && groups.map(group => {
            const collapsed = collapsedKeys.has(group.key);
            return (
              <React.Fragment key={group.key}>
                <GroupHeader
                  name={group.label}
                  count={group.rows.length}
                  rowCount={group.rowCount}
                  colSpan={columns.length}
                  collapsed={collapsed}
                  onToggle={() => toggleGroup(group.key)}
                />
                {!collapsed && group.rows.map((row, i) =>
                  renderRow({ ...row, __grouped: true }, i)
                )}
              </React.Fragment>
            );
          })}

          {rows.length === 0 && (!groups || groups.length === 0) && (
            <tr>
              <td colSpan={columns.length} style={{ padding: "32px 12px", textAlign: "center", color: "var(--text-4)" }}>
                No data yet
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

// ── Flash notification ────────────────────────────────────────────────────────
function Flash({ messages, onDismiss }) {
  if (!messages.length) return null;
  return (
    <div style={{ position: "fixed", bottom: 20, right: 20, zIndex: 9999, display: "flex", flexDirection: "column", gap: 8 }}>
      {messages.map(m => (
        <div key={m.id} style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "10px 14px", borderRadius: "var(--radius)",
          background: m.kind === "error" ? "rgba(248,113,113,.15)" : "rgba(52,211,153,.15)",
          border: `1px solid ${m.kind === "error" ? "var(--bad)" : "var(--good)"}`,
          color: m.kind === "error" ? "var(--bad)" : "var(--good)",
          fontSize: 13, maxWidth: 360,
        }}>
          <Icon name={m.kind === "error" ? "warn" : "check"} size={14} />
          <span style={{ flex: 1 }}>{m.text}</span>
          <button onClick={() => onDismiss(m.id)} style={{ background: "none", border: "none", cursor: "pointer", color: "inherit", lineHeight: 1 }}>
            <Icon name="close" size={12} />
          </button>
        </div>
      ))}
    </div>
  );
}

// ── Drawer ────────────────────────────────────────────────────────────────────
function Drawer({ open, onClose, title, children, width = 420 }) {
  return (
    <>
      {open && (
        <div onClick={onClose} style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,.4)", zIndex: 100,
        }} />
      )}
      <div style={{
        position: "fixed", top: 0, right: 0, bottom: 0, width,
        background: "var(--panel)", borderLeft: "1px solid var(--border)",
        transform: open ? "translateX(0)" : "translateX(100%)",
        transition: "transform .2s cubic-bezier(.4,0,.2,1)",
        zIndex: 101, display: "flex", flexDirection: "column",
      }}>
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "16px 20px", borderBottom: "1px solid var(--border)", flexShrink: 0,
        }}>
          <span style={{ fontWeight: 600, fontSize: 15 }}>{title}</span>
          <Btn variant="ghost" size="sm" icon="close" onClick={onClose} />
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>
          {children}
        </div>
      </div>
    </>
  );
}

// ── LoadingState ──────────────────────────────────────────────────────────────
function LoadingState({ label = "Loading…", size = 22 }) {
  return (
    <div style={{
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      gap: 8, padding: "40px 0", color: "var(--text-3)",
    }}>
      <Spinner size={size} />
      <span style={{ fontSize: 12, letterSpacing: ".02em" }}>{label}</span>
    </div>
  );
}

// ── InlineError ───────────────────────────────────────────────────────────────
function InlineError({ children, variant = "field", onDismiss, style }) {
  if (!children) return null;
  if (variant === "panel") {
    return (
      <div style={{
        display: "flex", alignItems: "flex-start", gap: 8,
        padding: "10px 14px", borderRadius: "var(--radius)",
        background: "rgba(248,113,113,.08)", border: "1px solid var(--bad)",
        color: "var(--bad)", fontSize: 13, ...style,
      }}>
        <Icon name="warn" size={14} style={{ flexShrink: 0, marginTop: 1 }} />
        <span style={{ flex: 1 }}>{children}</span>
        {onDismiss && (
          <button onClick={onDismiss} style={{ background: "none", border: "none", cursor: "pointer", color: "inherit", lineHeight: 1, padding: 0 }}>
            <Icon name="close" size={12} />
          </button>
        )}
      </div>
    );
  }
  // variant === "field"
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 5,
      color: "var(--bad)", fontSize: 12, ...style,
    }}>
      <Icon name="warn" size={12} style={{ flexShrink: 0 }} />
      <span>{children}</span>
    </div>
  );
}

// ── Checkbox ──────────────────────────────────────────────────────────────────
function Checkbox({ checked, onChange, disabled }) {
  const [hover, setHover] = useState(false);
  return (
    <span role="checkbox" aria-checked={checked}
      tabIndex={disabled ? -1 : 0}
      onClick={() => !disabled && onChange && onChange(!checked)}
      onKeyDown={e => { if ((e.key === " " || e.key === "Enter") && !disabled)
        { e.preventDefault(); onChange && onChange(!checked); } }}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{ width: 17, height: 17, flexShrink: 0, display: "inline-flex",
        alignItems: "center", justifyContent: "center",
        borderRadius: "var(--radius)",
        background: checked ? "var(--accent)" : "var(--panel-2)",
        border: "1px solid " + (checked ? "transparent"
          : hover && !disabled ? "var(--accent-line)" : "var(--border)"),
        color: "var(--accent-ink)",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.45 : 1,
        transition: "background .12s, border-color .12s" }}>
      {checked && <Icon name="check" size={12} />}
    </span>
  );
}

// ── OrderBadge ────────────────────────────────────────────────────────────────
function OrderBadge({ n, dragging, ...dragProps }) {
  const [hover, setHover] = useState(false);
  const bg = dragging ? "var(--accent-soft)" : hover ? "var(--hover)" : "var(--panel-3)";
  const border = dragging || hover ? "var(--accent-line)" : "var(--border)";
  const ink = dragging ? "var(--accent)" : "var(--text-2)";
  return (
    <span {...dragProps} title="Drag to reorder"
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{ width: 26, height: 26, flexShrink: 0, cursor: "grab",
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        borderRadius: "var(--radius)", background: bg,
        border: "1px solid " + border, color: ink,
        transition: "background .12s, border-color .12s, color .12s" }}>
      {hover && !dragging
        ? <Icon name="drag" size={16} style={{ color: "var(--text-3)" }} />
        : <span className="mono" style={{ fontSize: 12, fontWeight: 600,
            lineHeight: 1 }}>{n}</span>}
    </span>
  );
}

window.__UI__ = { Icon, Btn, KindTag, StatusPill, SourceBadge, DataTable, Flash, Drawer, Spinner, LoadingState, InlineError, GroupHeader, Checkbox, OrderBadge };
