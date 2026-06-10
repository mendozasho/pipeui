// Results screen — F2-A: unified card grid (walking skeleton)
// Replaces F1 By Source / By Function sub-tab model.
// Each run appends a result card (most-recent-first).
// Cards show face only; expand detail is a stub (Slice B).
const { useState, useEffect } = React;

// ── Type tag badge ────────────────────────────────────────────────────────────
function TypeTag({ cardType }) {
  const isValidation = cardType === "validation";
  return (
    <span style={{
      display: "inline-block", padding: "2px 8px", borderRadius: 99,
      background: isValidation ? "var(--check-bg, rgba(59,130,246,.12))" : "var(--xform-bg, rgba(245,158,11,.12))",
      color: isValidation ? "var(--check, #3b82f6)" : "var(--xform, #f59e0b)",
      fontSize: 11, fontWeight: 600, letterSpacing: ".03em",
    }}>
      {isValidation ? "validation" : "transform"}
    </span>
  );
}

// ── Summary line ──────────────────────────────────────────────────────────────
function SummaryLine({ card }) {
  if (card.card_type === "validation") {
    const { rows_passed, rows_failed, pass_rate } = card.summary;
    const total = (rows_passed ?? 0) + (rows_failed ?? 0);
    return (
      <div style={{ display: "flex", gap: 16, fontSize: 12, color: "var(--text-2)" }}>
        <span>
          <span style={{ color: "var(--good)", fontWeight: 600 }}>{(rows_passed ?? 0).toLocaleString()}</span>
          {" passed"}
        </span>
        <span>
          <span style={{ color: rows_failed > 0 ? "var(--bad)" : "var(--text-3)", fontWeight: 600 }}>
            {(rows_failed ?? 0).toLocaleString()}
          </span>
          {" failed"}
        </span>
        {pass_rate !== null && pass_rate !== undefined && (
          <span style={{ color: "var(--text-3)" }}>
            {(pass_rate * 100).toFixed(1)}% pass rate
          </span>
        )}
        {total === 0 && (
          <span style={{ color: "var(--text-4)" }}>no counts</span>
        )}
      </div>
    );
  }
  if (card.card_type === "transform") {
    const { rows_affected } = card.summary;
    return (
      <div style={{ fontSize: 12, color: "var(--text-2)" }}>
        <span style={{ fontWeight: 600 }}>{(rows_affected ?? 0).toLocaleString()}</span>
        {" rows affected"}
      </div>
    );
  }
  return null;
}

// ── Single result card ────────────────────────────────────────────────────────
function ResultCard({ card, highlighted }) {
  const [expanded, setExpanded] = useState(false);

  const ts = card.run_at ? new Date(card.run_at).toLocaleString() : "";

  return (
    <div style={{
      background: "var(--panel)",
      border: highlighted
        ? "1.5px solid var(--accent)"
        : "1px solid var(--border)",
      borderRadius: "var(--radius-lg)",
      overflow: "hidden",
      boxShadow: highlighted ? "0 0 0 2px var(--accent-soft)" : "none",
    }}>
      {/* Card face */}
      <div style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: "14px 18px",
      }}>
        {/* Chevron */}
        <button
          onClick={() => setExpanded(e => !e)}
          style={{
            background: "none", border: "none", cursor: "pointer",
            color: "var(--text-4)", fontSize: 13, padding: 0, flexShrink: 0,
            lineHeight: 1,
          }}
          title="Expand detail"
        >
          {expanded ? "▾" : "▸"}
        </button>

        {/* Source name */}
        <div style={{ fontWeight: 600, fontSize: 14, flex: 1, minWidth: 0 }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block" }}>
            {card.source_name}
          </span>
        </div>

        {/* Type tag */}
        <TypeTag cardType={card.card_type} />

        {/* Timestamp */}
        <span style={{ fontSize: 11, color: "var(--text-4)", whiteSpace: "nowrap", flexShrink: 0 }}>
          {ts}
        </span>
      </div>

      {/* Summary line */}
      <div style={{ padding: "0 18px 14px 44px" }}>
        <SummaryLine card={card} />
      </div>

      {/* Expand stub */}
      {expanded && (
        <div style={{
          borderTop: "1px solid var(--border)",
          padding: "12px 18px",
          color: "var(--text-3)", fontSize: 13,
          background: "var(--panel-2)",
        }}>
          Detail coming soon.
        </div>
      )}
    </div>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────
function ScreenResults({ flash, resultCards, resultsContext }) {
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* Header */}
      <div style={{
        padding: "16px 24px", borderBottom: "1px solid var(--border)", flexShrink: 0,
      }}>
        <div style={{ fontWeight: 600, fontSize: 16 }}>Results</div>
        <div style={{ color: "var(--text-3)", fontSize: 12 }}>
          {resultCards.length > 0
            ? `${resultCards.length} run${resultCards.length !== 1 ? "s" : ""} in this session`
            : "Run a pipeline to see results here"}
        </div>
      </div>

      {/* Card grid */}
      <div style={{ flex: 1, overflow: "auto", padding: 24 }}>
        {resultCards.length === 0 ? (
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            height: "100%", color: "var(--text-4)", fontSize: 14, textAlign: "center",
          }}>
            Run a pipeline from the Data or Functions screen to see results here.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 800 }}>
            {resultCards.map(card => {
              const highlighted = resultsContext && resultsContext.source_id
                ? card.source_id === resultsContext.source_id &&
                  card === resultCards.find(c => c.source_id === resultsContext.source_id)
                : false;
              return (
                <ResultCard
                  key={card.run_id}
                  card={card}
                  highlighted={highlighted}
                />
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

window.__ScreenResults__ = ScreenResults;
