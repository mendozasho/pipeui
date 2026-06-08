// Mock data — Phase A: SOURCES and REPORTS are replaced by real fetch() calls.
// Phases B-E placeholders remain until their phases ship.

// Phase D placeholder
const MODULES = [
  {
    id: "mod-1",
    name: "normalise.py",
    functions: [
      {
        id: "fn-1", name: "trim_whitespace", kind: "transform",
        doc: "Strip leading and trailing whitespace from a string column.",
        sig: "(col: pd.Series) -> pd.Series",
        params: [{ name: "col", type: "pd.Series" }],
      },
      {
        id: "fn-2", name: "flag_nulls", kind: "validation",
        doc: "Return True where the value is null or empty.",
        sig: "(col: pd.Series) -> pd.Series[bool]",
        params: [{ name: "col", type: "pd.Series" }],
      },
    ],
  },
];

// Phase E placeholder
const FUNCTION_SETS = [
  { id: "fs-1", source_id: "src-1", steps: [] },
];

window.__DATA__ = { MODULES, FUNCTION_SETS };
