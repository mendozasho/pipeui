# PipeUI — Data Screen Source-List Redesign (Session 2)

Design brief for `feat/data-source-list-redesign`. This is the implementation
reference **and** the text to paste into the issue body below the line. Live,
interactive version: `PipeUI - Data Screen Layout Spec (Session 2).html`.

Components live in `frontend/ui.jsx` (exported via `window.__UI__`) and
`frontend/screen-data.jsx`.

## Constraints (apply throughout)

- Inline styles only — no CSS classes, no stylesheet additions.
- No new external dependencies.
- **No new colour tokens** — every element maps to an existing `index.html` token.
- One new shared component (`GroupHeader`), one new `Icon` glyph (`group`).
- `DataTable`'s new `groups` prop is **optional** — Functions / Sets / Results
  screens that use `DataTable` are untouched.

Token reference (defined in `index.html`):

```
bg      --bg #0e0e10 · --panel #141416 · --panel-2 #1a1a1d · --panel-3 #202024
border  --border rgba(255,255,255,.08) · --border-soft rgba(255,255,255,.04) · --hover rgba(255,255,255,.06)
text    --text #f0f0f2 · --text-2 #a0a0aa · --text-3 #6a6a74 · --text-4 #3a3a42
semantic --good #34d399 · --bad #f87171 · --warn #fbbf24 · --run #60a5fa
radius  --radius 6px · --radius-lg 10px      font  Geist / Geist Mono
```

---

# Decision 1 — Table, not a card grid

**Keep the `DataTable` foundation. Redesign the row composition and add group
bands — do not switch to a card grid.**

The screen's job is to let an analyst scan and compare many *homogeneous*
sources — the same four attributes each, read down a column. Three forces
settle it for the table:

1. **Comparison down a column.** Analysts compare row counts and recency across
   sources. A shared right-aligned, tabular-numeral column makes magnitudes
   scan instantly; a card grid scatters the same number into a 2-D layout where
   the eye can't line values up.
2. **Grouping needs full-width bands.** #112 introduces pattern groups. A table
   renders a group header as one `colSpan` row that members nest under —
   structurally clean. A card grid has to fake this with section wrappers that
   break grid flow and re-flow awkwardly on resize.
3. **Density is the explicit goal.** A table row is ~38px; an equivalent card is
   ~96px and spends horizontal space on chrome. With 30+ sources the table shows
   2–3× more per screen.

---

# Decision 2 — Prominence hierarchy

Three tiers, each pinned to a token. Carried entirely by weight / size /
colour-tier / alignment — no new colours.

| Tier | Datum | Treatment |
|---|---|---|
| **Primary** | source name | `SourceBadge` + name at `--text`, weight 500, 13.5px. Column-count subline beneath at `--text-3` mono. The identifier the analyst hunts for. |
| **Secondary** | row count | Mono, `tabular-nums`, weight 500, **right-aligned**. `--text` when ingested, `--text-4` em-dash when not. The size/health signal — aligned so magnitudes compare down the column. |
| **Tertiary** | last ingested + status | Date at `--text-3`; existing `StatusPill` (`ingested`→`--good`, `registered`→`--text-3`). Supporting context; never competes with name or count. |

**Remove the `Method` column.** `ingestion_method` is registration config,
already shown in the source drawer's "Ingestion" KV row. It is not a scannable
status. Cutting it tightens every row and sharpens the three tiers.

---

# Decision 3 — Source grouping (#112)

Per `CONTEXT.md` → *source group*: a source whose `pattern` is non-null is a
named report rendered human-readably (e.g. `sales_jan_\d+` → `sales_jan_*`); the
raw regex is never shown. In the list, sources sharing a pattern cluster under a
full-span band; sources with no pattern render flat above the groups.

## 3a · `GroupHeader` — new shared primitive

A `<tr>` so it lives inside the existing `<table>`; member rows follow beneath
it, indented. Collapsible — the core density lever for many sources. Collapse
state is owned by `DataTable`, not `GroupHeader`.

| Prop | Type | Default | Notes |
|---|---|---|---|
| `name` | string | — | Human-readable pattern label, e.g. `"sales_jan_*"`. |
| `count` | number | — | Sources in the group → `"N sources"` chip. |
| `rowCount` | number | — | Summed ingested rows across members → right-aligned aggregate. |
| `colSpan` | number | — | Pass `columns.length` so the band spans full width. |
| `collapsed` | bool | `false` | Hides member rows; rotates chevron −90°. |
| `onToggle` | function | — | Fired by the chevron button to flip `collapsed`. |

**Tokens.** Band background `--panel-2` (one step up from the row plane),
`--border` top rule, `--border-soft` bottom — quieter than the table head,
louder than a member row. Label `--text-2` mono. Count chip reuses the existing
`--panel-3 / --border` chip recipe at `--text-3`. Aggregate `--text-3` mono,
`tabular-nums`. Chevron + group glyph at `--text-3`.

```jsx
// NEW · ui.jsx
function GroupHeader({ name, count, rowCount, colSpan, collapsed, onToggle }) {
  return (
    <tr>
      <td colSpan={colSpan} style={{
        background: "var(--panel-2)",
        borderTop: "1px solid var(--border)",
        borderBottom: "1px solid var(--border-soft)",
        padding: "7px 12px",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button onClick={onToggle} aria-expanded={!collapsed}
            aria-label={collapsed ? "Expand group" : "Collapse group"}
            style={{ background: "none", border: "none", cursor: "pointer",
              color: "var(--text-3)", padding: 0, display: "inline-flex",
              transform: collapsed ? "rotate(-90deg)" : "none",
              transition: "transform .15s" }}>
            <Icon name="chevron" size={16} />
          </button>
          <Icon name="group" size={15} style={{ color: "var(--text-3)" }} />
          <span className="mono" style={{ fontSize: 12.5, color: "var(--text-2)", fontWeight: 500 }}>{name}</span>
          <span style={{ fontSize: 11, color: "var(--text-3)", background: "var(--panel-3)",
            border: "1px solid var(--border)", borderRadius: 99, padding: "1px 8px",
            whiteSpace: "nowrap" }}>{count} sources</span>
          <span className="mono" style={{ marginLeft: "auto", fontSize: 12, color: "var(--text-3)",
            fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>{rowCount.toLocaleString()} rows</span>
        </div>
      </td>
    </tr>
  );
}

// NEW glyph — add to the ICONS map in ui.jsx:
group: <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5">
  <path d="M10 2.5 3 6l7 3.5L17 6l-7-3.5z"/><path d="m3 10 7 3.5L17 10"/><path d="m3 13.5 7 3.5 7-3.5"/>
</svg>,
```

## 3b · `DataTable` gains an optional `groups` prop

Extend the shared component rather than fork a new list. Omit `groups` and
behaviour is identical to today. Provide it and grouped bands render after the
flat rows. Member rows get a left indent (`paddingLeft: 34`).

| Prop | Type | Default | Notes |
|---|---|---|---|
| `groups` | `Group[] \| undefined` | `undefined` | Omitted → unchanged. |
| `Group.key` | string | — | Stable identity for collapse state + React keys. |
| `Group.label` | string | — | Passed to `GroupHeader name`. |
| `Group.rows` | `Row[]` | — | Member rows, same `columns`, indented. |
| `Group.rowCount` | number | — | Pre-summed aggregate for the band. |

```jsx
// MODIFY · ui.jsx
function DataTable({ columns, rows, groups, onRowClick, selectedId }) {
  const [collapsed, setCollapsed] = useState({});            // group key -> bool
  const toggle = k => setCollapsed(c => ({ ...c, [k]: !c[k] }));

  const renderRow = (row, key, indent) => (
    <tr key={key} onClick={() => onRowClick && onRowClick(row)}
        /* …existing hover + selected styling, unchanged… */ >
      {columns.map(c => (
        <td key={c.key} style={{ padding: "9px 12px",
          paddingLeft: indent ? 34 : 12, color: "var(--text)", verticalAlign: "middle" }}>
          {c.render ? c.render(row[c.key], row) : row[c.key]}
        </td>
      ))}
    </tr>
  );

  return (
    <div style={{ overflowX: "auto" }}>
      <table /* …unchanged head… */>
        <tbody>
          {rows.map((r, i) => renderRow(r, r.id ?? i, false))}
          {(groups || []).map(g => (
            <React.Fragment key={g.key}>
              <GroupHeader name={g.label} count={g.rows.length} rowCount={g.rowCount}
                colSpan={columns.length} collapsed={collapsed[g.key]}
                onToggle={() => toggle(g.key)} />
              {!collapsed[g.key] && g.rows.map((r, i) => renderRow(r, g.key + i, true))}
            </React.Fragment>
          ))}
          {rows.length === 0 && (groups || []).length === 0 && (
            /* …existing "No data yet" empty row… */
          )}
        </tbody>
      </table>
    </div>
  );
}
```

---

# Decision 4 — Wiring in `screen-data.jsx`

Three edits in `ScreenData()`. Grouping is **display-only** — no schema or
endpoint change; it reads the `pattern` the source already carries.

```jsx
// 1 · DELETE the "Method" column from `columns`:
//   { key: "ingestion_method", label: "Method",
//     render: v => <span style={{ fontSize: 12, color: "var(--text-3)" }}>{v}</span> },

// 2 · Partition sources by pattern label (null pattern → flat):
const grouped = {}, flat = [];
for (const s of sources) {
  if (s.pattern_label) (grouped[s.pattern_label] ??= []).push(s);
  else flat.push(s);
}
const groups = Object.entries(grouped).map(([label, rows]) => ({
  key: label, label,
  rows: rows.map(s => ({ ...s, id: s.source_id })),
  rowCount: rows.reduce((n, s) => n + (s.date_ingested ? (s.row_count || 0) : 0), 0),
}));

// 3 · Pass both to the (back-compatible) DataTable:
<DataTable
  columns={columns}
  rows={flat.map(s => ({ ...s, id: s.source_id }))}
  groups={groups}
  onRowClick={row => setSelectedSource(row)}
  selectedId={selectedSource?.source_id}
/>
```

**Backend note.** The partition keys on a human-readable `pattern_label`. If
`GET /sources` doesn't return one yet, either derive it client-side from the
source's `pattern` regex or add a `pattern_label` field server-side — coordinate
with #112. Sources with no pattern stay flat; with no groups at all, the table
renders exactly as the flat list does today.

---

# Export registration

```jsx
// end of ui.jsx
window.__UI__ = {
  Icon, Btn, KindTag, StatusPill, SourceBadge, DataTable, Flash, Drawer,
  Spinner, LoadingState, InlineError,   // Session 1
  GroupHeader,                          // ← added this session
};
```

---

# Acceptance criteria → where satisfied

- [ ] **Source list renders with the new layout** — 3-tier rows, `Method`
  dropped, group bands. (Decision 1 + 2)
- [ ] **All existing functionality preserved** — row click → drawer, `StatusPill`,
  row count, last-ingested date all retained. (Decision 2)
- [ ] **#112 group headers visually integrated** — `GroupHeader` as a `colSpan`
  `<tr>`, collapsible, aggregate counts. (Decision 3)
- [ ] **No new external dependencies** — inline styles, existing tokens, one SVG
  glyph. (Constraints)

---

# Out of scope (follow-up)

- **Auto-infer files on disk** matching a pattern + bulk-ingest with a
  confirmation list (noted as future in `CONTEXT.md` → *source group*). Not part
  of this slice.
- **Column-mismatch confirmation popup on ingest** is the other half of #112 and
  is tracked there — this brief covers the *list display* only.
- **Sort / filter controls** on the source list — not requested; revisit if the
  density work surfaces a need.
