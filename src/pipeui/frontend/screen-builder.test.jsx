// Component render-state tests for the Report Builder — targets #157's actual
// component structure (PendingStepCard, ParamRow, StepCard).
//
// Harness: vitest + jsdom (see vitest.config.js, test-setup.js). Dev-time only —
// the app itself runs no-build-step from CDN React.
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent, within, waitFor, act } from "@testing-library/react";
import { PendingStepCard, ParamRow, StepCard, JoinModal, FilterModal, RenameModal, PaletteBuiltinCard, PaletteBuiltinDrawer, BuiltinStepCard, PipelineCanvas } from "./screen-builder.jsx";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Dry-run param fixtures (shape returned by suggest_bindings)
// ---------------------------------------------------------------------------

function strParam(overrides = {}) {
  return {
    param_id: "p-str",
    param_name: "label",
    param_type: "str",
    param_kind: "column",
    function_name: "fn_alpha",
    suggested_columns: [],
    current_scalar_value: null,
    ...overrides,
  };
}

function scalarParam(overrides = {}) {
  return {
    param_id: "p-int",
    param_name: "threshold",
    param_type: "int",
    param_kind: "scalar",
    function_name: "fn_alpha",
    suggested_columns: [],
    current_scalar_value: null,
    ...overrides,
  };
}

const SOURCE_COLUMNS = [
  { column_id: "c1", column_name: "amount", column_type: "DOUBLE" },
  { column_id: "c2", column_name: "region", column_type: "VARCHAR" },
];

function renderCard(params, extra = {}) {
  return render(
    React.createElement(PendingStepCard, {
      dryRunResult: { params, available_columns: SOURCE_COLUMNS },
      stepName: "Step",
      onSave: () => {},
      onCancel: () => {},
      saving: false,
      saveError: null,
      ...extra,
    })
  );
}

// ---------------------------------------------------------------------------
// Harness smoke
// ---------------------------------------------------------------------------

describe("harness", () => {
  it("imports components and renders", () => {
    renderCard([]);
    expect(screen.getByText("Step")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// PendingStepCard — str "Plain string" / "Column-backed" toggle (#157 feature)
// ---------------------------------------------------------------------------

describe("PendingStepCard — str param mode toggle", () => {
  it("renders Plain string / Column-backed toggle for a str param", () => {
    renderCard([strParam()]);
    expect(screen.getByText("Plain string")).toBeTruthy();
    expect(screen.getByText("Column-backed")).toBeTruthy();
  });

  it("defaults to Plain string mode showing a text input, not a column list", () => {
    renderCard([strParam()]);
    // In plain-string mode the scalar text input is shown (param_kind scalar OR str-text).
    // The column-list 'Bind column(s)' helper text should not be present.
    expect(screen.queryByText(/Bind column\(s\)/)).toBeNull();
  });

  it("switching to Column-backed reveals the column binding list", () => {
    renderCard([strParam()]);
    fireEvent.click(screen.getByText("Column-backed"));
    expect(screen.getByText(/Bind column\(s\)/)).toBeTruthy();
    expect(screen.getByText("amount")).toBeTruthy();
    expect(screen.getByText("region")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// PendingStepCard — scalar value entry + save payload (Bug #186 (1))
// ---------------------------------------------------------------------------

describe("PendingStepCard — scalar values", () => {
  it("renders a text input for a scalar (int) param", () => {
    renderCard([scalarParam()]);
    const input = screen.getByPlaceholderText("int");
    expect(input).toBeTruthy();
  });

  it("pre-populates a scalar input from current_scalar_value", () => {
    renderCard([scalarParam({ current_scalar_value: "11" })]);
    const input = screen.getByPlaceholderText("int");
    expect(input.value).toBe("11");
  });

  it("onSave receives the scalar value in the filledScalars map", () => {
    const onSave = vi.fn();
    renderCard([scalarParam()], { onSave });
    const input = screen.getByPlaceholderText("int");
    fireEvent.change(input, { target: { value: "11" } });
    // No required column params → Save is enabled
    fireEvent.click(screen.getByText("Save"));
    expect(onSave).toHaveBeenCalledTimes(1);
    const [bindings, scalars] = onSave.mock.calls[0];
    expect(bindings).toEqual([]);
    expect(scalars).toEqual({ "p-int": "11" });
  });

  it("onSave sends a blank scalar as empty string so a cleared field reverts to default", () => {
    // A blank is sent (not omitted) so the backend clears any persisted override on
    // edit; attach skips blanks, PATCH deletes the row. (#191)
    const onSave = vi.fn();
    renderCard([scalarParam()], { onSave });
    fireEvent.click(screen.getByText("Save"));
    const [, scalars] = onSave.mock.calls[0];
    expect(scalars).toEqual({ "p-int": "" });
  });

  it("a str param's plain-string literal is sent as a scalar value, not a binding", () => {
    const onSave = vi.fn();
    renderCard([strParam()], { onSave });
    // Plain string is the default mode; a text input is rendered for str
    const input = screen.getByPlaceholderText("str");
    fireEvent.change(input, { target: { value: "hello" } });
    fireEvent.click(screen.getByText("Save"));
    const [bindings, scalars] = onSave.mock.calls[0];
    expect(bindings).toEqual([]);
    expect(scalars).toEqual({ "p-str": "hello" });
  });
});

// ---------------------------------------------------------------------------
// PendingStepCard — edit-state restore on re-open (#191/#192)
// ---------------------------------------------------------------------------

describe("PendingStepCard — edit-state restore", () => {
  it("pre-populates a str plain-string input from current_scalar_value", () => {
    // The bug: str values disappeared on edit because they were never seeded.
    renderCard([strParam({ current_scalar_value: "hello" })]);
    const input = screen.getByPlaceholderText("str");
    expect(input.value).toBe("hello");
  });

  it("opens a str param in Column-backed mode when it has saved column bindings", () => {
    renderCard([strParam({ current_bindings: [{ column_id: "c1", column_name: "amount" }] })]);
    // Column list is shown (column mode) rather than the plain-string text input.
    expect(screen.getByText(/Bind column\(s\)/)).toBeTruthy();
    expect(screen.queryByPlaceholderText("str")).toBeNull();
  });

  it("restores saved column selections from current_bindings on edit", () => {
    const onSave = vi.fn();
    renderCard([strParam({ current_bindings: [{ column_id: "c2", column_name: "region" }] })], { onSave });
    fireEvent.click(screen.getByText("Save"));
    const [bindings] = onSave.mock.calls[0];
    expect(bindings).toEqual([{ param_id: "p-str", column_ids: ["c2"] }]);
  });

  it("clears the stale plain-string value when a str switches text→column (Finding 1)", () => {
    // str opens in Plain string mode holding "foo"; user switches to Column-backed
    // and binds a column. The save must clear the scalar so the binding is the single
    // source of truth — otherwise "foo" lingers in source_scalar_map.
    const onSave = vi.fn();
    renderCard([strParam({ current_scalar_value: "foo" })], { onSave });
    fireEvent.click(screen.getByText("Column-backed"));
    fireEvent.click(screen.getByText("amount"));
    fireEvent.click(screen.getByText("Save"));
    const [bindings, scalars] = onSave.mock.calls[0];
    expect(bindings).toEqual([{ param_id: "p-str", column_ids: ["c1"] }]);
    expect(scalars).toEqual({ "p-str": "" });
  });
});

// ---------------------------------------------------------------------------
// PendingStepCard — mapping modal fixes (#188 label, #189 click, #190 grouping)
// ---------------------------------------------------------------------------

describe("PendingStepCard — mapping modal", () => {
  it("shows the function description above its params (#188)", () => {
    renderCard([scalarParam({ function_doc: "Adds a threshold" })]);
    expect(screen.getByText("Adds a threshold")).toBeTruthy();
  });

  it("groups params under one header per function (#190)", () => {
    renderCard([
      scalarParam({ param_id: "a", param_name: "x", function_name: "fn_one" }),
      strParam({ param_id: "b", param_name: "y", function_name: "fn_two" }),
    ]);
    expect(screen.getByText("fn_one")).toBeTruthy();
    expect(screen.getByText("fn_two")).toBeTruthy();
  });

  it("a single click on a binding row toggles exactly once — no double-toggle (#189)", () => {
    const onSave = vi.fn();
    renderCard([strParam()], { onSave });
    fireEvent.click(screen.getByText("Column-backed"));   // enter column mode
    fireEvent.click(screen.getByText("amount"));          // one click selects c1
    fireEvent.click(screen.getByText("Save"));
    const [bindings] = onSave.mock.calls[0];
    expect(bindings).toEqual([{ param_id: "p-str", column_ids: ["c1"] }]);
  });
});

// ---------------------------------------------------------------------------
// ParamRow — placed-step card scalar display (Bug #186 (2))
// ---------------------------------------------------------------------------

describe("ParamRow — placed step param display", () => {
  it("shows '= <value>' for a scalar param with a persisted scalar_value", () => {
    render(
      React.createElement(ParamRow, {
        param: {
          param_id: "p-int",
          param_name: "threshold",
          param_type: "int",
          bindings: [],
          scalar_value: "11",
        },
      })
    );
    expect(screen.getByText("= 11")).toBeTruthy();
    expect(screen.queryByText("unbound")).toBeNull();
  });

  it("shows 'unbound' for a param with no binding and no scalar_value", () => {
    render(
      React.createElement(ParamRow, {
        param: {
          param_id: "p-int",
          param_name: "threshold",
          param_type: "int",
          bindings: [],
          scalar_value: null,
        },
      })
    );
    expect(screen.getByText("unbound")).toBeTruthy();
  });

  it("shows the bound column name when a binding is present", () => {
    render(
      React.createElement(ParamRow, {
        param: {
          param_id: "p-col",
          param_name: "col",
          param_type: "pd.Series",
          bindings: [{ column_id: "c1", column_name: "amount" }],
          scalar_value: null,
        },
      })
    );
    expect(screen.getByText("amount")).toBeTruthy();
    expect(screen.queryByText("unbound")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// StepCard — edit button wired to onEdit (#157 feature)
// ---------------------------------------------------------------------------

function makeStep(overrides = {}) {
  return {
    source_function_map_id: "sfm-1",
    set_id: "set-1",
    set_name: "fn_alpha",
    position: 0,
    output_mode: "append",
    functions: [
      {
        function_id: "fn-1",
        function_name: "fn_alpha",
        function_type: "transform",
        params: [],
      },
    ],
    ...overrides,
  };
}

describe("StepCard — edit button", () => {
  it("renders an edit button that calls onEdit with the step", () => {
    const onEdit = vi.fn();
    render(
      React.createElement(StepCard, {
        step: makeStep(),
        sourceId: "src-1",
        order: 1,
        onRemoved: () => {},
        isDragging: false,
        onDragStart: () => {},
        onDragEnd: () => {},
        onDragOver: () => {},
        resultTag: null,
        onNavigateResults: () => {},
        onEdit,
      })
    );
    // The edit button carries the title "Edit step bindings"
    const editBtn = screen.getByTitle("Edit step bindings");
    fireEvent.click(editBtn);
    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(onEdit.mock.calls[0][0].source_function_map_id).toBe("sfm-1");
  });
});

// ===========================================================================
// JoinModal — two-step join source picker (#152)
// ===========================================================================

const LEFT_SOURCE = {
  source_id: "L",
  source_name: "sales",
  columns: [
    { column_id: "l1", column_name: "region", column_type: "VARCHAR" },
    { column_id: "l2", column_name: "amount", column_type: "DOUBLE" },
  ],
};

// regions: no pipeline steps; targets: 2 pipeline steps (use-transformed eligible)
const SOURCES = [
  LEFT_SOURCE,
  {
    source_id: "R1",
    source_name: "regions",
    row_count: 12,
    columns: [
      { column_id: "r1", column_name: "region", column_type: "VARCHAR" },
      { column_id: "r2", column_name: "label", column_type: "VARCHAR" },
    ],
    steps: 0,
  },
  {
    source_id: "R2",
    source_name: "targets",
    row_count: 5,
    columns: [{ column_id: "t1", column_name: "region", column_type: "VARCHAR" }],
    steps: 2,
  },
];

const RIGHT_COLUMNS = [
  { column_id: "r1", column_name: "region", column_type: "VARCHAR" },
  { column_id: "r2", column_name: "label", column_type: "VARCHAR" },
];

function renderJoin(extra = {}) {
  const fetchRightColumns =
    extra.fetchRightColumns || vi.fn(() => Promise.resolve(RIGHT_COLUMNS));
  const onSubmit = extra.onSubmit || vi.fn(() => Promise.resolve({ ok: true, step_id: "s1" }));
  const onClose = extra.onClose || vi.fn();
  const utils = render(
    React.createElement(JoinModal, {
      open: true,
      onClose,
      currentSource: extra.currentSource || LEFT_SOURCE,
      sources: extra.sources || SOURCES,
      fetchRightColumns,
      onSubmit,
    })
  );
  return { ...utils, fetchRightColumns, onSubmit, onClose };
}

describe("JoinModal — Step 1 source picker (AC2)", () => {
  it("lists all sources except the current one", () => {
    renderJoin();
    expect(screen.getByText("regions")).toBeTruthy();
    expect(screen.getByText("targets")).toBeTruthy();
    expect(screen.queryByText("sales")).toBeNull();
  });

  it("shows row and column counts for each candidate", () => {
    renderJoin();
    const row = screen.getByTestId("source-row-R1");
    expect(within(row).getByText(/12 rows/)).toBeTruthy();
    expect(within(row).getByText(/2 cols/)).toBeTruthy();
  });

  it("renders the use-transformed toggle ONLY for sources with pipeline steps", () => {
    renderJoin();
    // targets has steps=2 -> toggle present + step count caption
    const withSteps = screen.getByTestId("source-row-R2");
    expect(within(withSteps).queryByTestId("switch")).toBeTruthy();
    expect(within(withSteps).getByText(/2 pipeline steps/)).toBeTruthy();
    // regions has steps=0 -> no toggle
    const noSteps = screen.getByTestId("source-row-R1");
    expect(within(noSteps).queryByTestId("switch")).toBeNull();
  });
});

describe("JoinModal — Step 1 empty state (AC8)", () => {
  it("shows the empty state when no other sources exist", () => {
    renderJoin({ sources: [LEFT_SOURCE] });
    expect(screen.getByText(/No other reports available/)).toBeTruthy();
  });

  it("disables Next in the empty state", () => {
    renderJoin({ sources: [LEFT_SOURCE] });
    expect(screen.getByText("Next").disabled).toBe(true);
  });
});

describe("JoinModal — Step 1 -> Step 2 transition (AC3)", () => {
  it("disables Next until a source is selected, then advances on Next", async () => {
    const { fetchRightColumns } = renderJoin();
    expect(screen.getByText("Next").disabled).toBe(true);
    fireEvent.click(screen.getByText("regions"));
    expect(screen.getByText("Next").disabled).toBe(false);
    fireEvent.click(screen.getByText("Next"));
    await screen.findByTestId("column-list-right");
    // Step 2 context line names both sides.
    expect(screen.getByText(/Joining/)).toBeTruthy();
    expect(fetchRightColumns).toHaveBeenCalledWith("R1", false);
  });
});

describe("JoinModal — Step 2 column pickers (AC4)", () => {
  it("populates left and right column lists with the correct column sets", async () => {
    renderJoin();
    fireEvent.click(screen.getByText("regions"));
    fireEvent.click(screen.getByText("Next"));
    const right = await screen.findByTestId("column-list-right");
    const left = screen.getByTestId("column-list-left");
    // Left = current source columns
    expect(within(left).getByText("amount")).toBeTruthy();
    // Right = fetched columns
    expect(within(right).getByText("label")).toBeTruthy();
  });
});

describe("JoinModal — Back preserves selection (AC5)", () => {
  it("returns to step 1 with the previously selected source still selected", async () => {
    renderJoin();
    fireEvent.click(screen.getByText("regions"));
    fireEvent.click(screen.getByText("Next"));
    await screen.findByTestId("column-list-right");
    fireEvent.click(screen.getByText("Back"));
    // Back on step 1: Next immediately enabled because regions is still selected.
    expect(screen.getByText("Next").disabled).toBe(false);
  });
});

describe("JoinModal — Save gating + payload (AC6, AC7)", () => {
  it("disables Add step until both columns of every key pair are selected", async () => {
    renderJoin();
    fireEvent.click(screen.getByText("regions"));
    fireEvent.click(screen.getByText("Next"));
    const right = await screen.findByTestId("column-list-right");
    const left = screen.getByTestId("column-list-left");
    expect(screen.getByText("Add step").disabled).toBe(true);
    fireEvent.click(within(left).getByText("amount"));
    expect(screen.getByText("Add step").disabled).toBe(true); // right still empty
    fireEvent.click(within(right).getByText("label"));
    expect(screen.getByText("Add step").disabled).toBe(false);
  });

  it("submits the correct builtin_config on Add step", async () => {
    const { onSubmit } = renderJoin();
    fireEvent.click(screen.getByText("regions"));
    fireEvent.click(screen.getByText("Next"));
    const right = await screen.findByTestId("column-list-right");
    const left = screen.getByTestId("column-list-left");
    fireEvent.click(within(left).getByText("region"));
    fireEvent.click(within(right).getByText("region"));
    fireEvent.click(screen.getByText("Add step"));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith({
      right_source_id: "R1",
      use_transformed: false,
      join_type: "inner",
      on: [{ left_col: "region", right_col: "region" }],
      keep_columns: "all",
    });
  });

  it("keeps both selections when left+right are chosen in one render batch", async () => {
    // Regression: KeyPairBuilder must use a functional state update. Two key-column
    // selections coalesced into a single React batch (no re-render between) read the
    // same `pairs` snapshot — a closure-based setPairs(pairs.map(...)) lets the second
    // clobber the first, leaving the pair half-filled. Verified live via Playwright.
    renderJoin();
    fireEvent.click(screen.getByText("regions"));
    fireEvent.click(screen.getByText("Next"));
    const right = await screen.findByTestId("column-list-right");
    const left = screen.getByTestId("column-list-left");
    const leftCol = within(left).getByText("amount");
    const rightCol = within(right).getByText("label");
    act(() => {
      leftCol.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      rightCol.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(screen.getByText("Add step").disabled).toBe(false);
  });
});

describe("JoinModal — Back -> Next preserves key pairs (#214)", () => {
  it("keeps configured key pairs when returning to step 2 with the same source", async () => {
    renderJoin();
    fireEvent.click(screen.getByText("regions"));
    fireEvent.click(screen.getByText("Next"));
    const right = await screen.findByTestId("column-list-right");
    const left = screen.getByTestId("column-list-left");
    fireEvent.click(within(left).getByText("region"));
    fireEvent.click(within(right).getByText("region"));
    expect(screen.getByText("Add step").disabled).toBe(false);
    // Round-trip: Back to step 1, then Next again with the SAME source.
    fireEvent.click(screen.getByText("Back"));
    fireEvent.click(screen.getByText("Next"));
    await screen.findByTestId("column-list-right");
    // The pair must survive the round-trip (it was silently wiped before #214).
    expect(screen.getByText("Add step").disabled).toBe(false);
  });

  it("resets key pairs when a different source is chosen on Back -> Next", async () => {
    renderJoin();
    fireEvent.click(screen.getByText("regions"));
    fireEvent.click(screen.getByText("Next"));
    const right = await screen.findByTestId("column-list-right");
    const left = screen.getByTestId("column-list-left");
    fireEvent.click(within(left).getByText("region"));
    fireEvent.click(within(right).getByText("region"));
    expect(screen.getByText("Add step").disabled).toBe(false);
    // Back, switch to a DIFFERENT source, Next -> pairs reset (columns differ).
    fireEvent.click(screen.getByText("Back"));
    fireEvent.click(screen.getByText("targets"));
    fireEvent.click(screen.getByText("Next"));
    await screen.findByTestId("column-list-right");
    expect(screen.getByText("Add step").disabled).toBe(true);
  });
});

// ===========================================================================
// JoinModal — use_transformed regression (runner-resolution-model slice 2 / #18)
//
// AC4 (frontend regression): JoinModal still sends use_transformed and requests
// the transformed column set when the toggle is on. Guards the live toggle so the
// resolve_frame backend (slice 2) actually receives transformed=true.
// ===========================================================================

describe("JoinModal — use_transformed toggle (slice 2 / #18 regression)", () => {
  it("requests the transformed column set (fetchRightColumns called with true) when the toggle is on", async () => {
    const { fetchRightColumns } = renderJoin();
    // R2 ("targets") has steps=2 -> the use-transformed toggle is rendered.
    const row = screen.getByTestId("source-row-R2");
    fireEvent.click(within(row).getByText("targets"));      // select R2
    fireEvent.click(within(row).getByTestId("switch"));     // flip use-transformed ON
    fireEvent.click(screen.getByText("Next"));
    await screen.findByTestId("column-list-right");
    // The modal must request the right source's TRANSFORMED columns.
    expect(fetchRightColumns).toHaveBeenCalledWith("R2", true);
  });

  it("submits use_transformed=true in builtin_config when the toggle is on", async () => {
    const { onSubmit } = renderJoin();
    const row = screen.getByTestId("source-row-R2");
    fireEvent.click(within(row).getByText("targets"));
    fireEvent.click(within(row).getByTestId("switch"));     // use-transformed ON
    fireEvent.click(screen.getByText("Next"));
    const right = await screen.findByTestId("column-list-right");
    const left = screen.getByTestId("column-list-left");
    fireEvent.click(within(left).getByText("region"));
    fireEvent.click(within(right).getByText("region"));
    fireEvent.click(screen.getByText("Add step"));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ right_source_id: "R2", use_transformed: true })
    );
  });

  it("still requests raw columns (use_transformed false) when the toggle is off", async () => {
    const { fetchRightColumns, onSubmit } = renderJoin();
    fireEvent.click(screen.getByText("regions"));            // R1, no toggle
    fireEvent.click(screen.getByText("Next"));
    const right = await screen.findByTestId("column-list-right");
    const left = screen.getByTestId("column-list-left");
    expect(fetchRightColumns).toHaveBeenCalledWith("R1", false);
    fireEvent.click(within(left).getByText("region"));
    fireEvent.click(within(right).getByText("region"));
    fireEvent.click(screen.getByText("Add step"));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ use_transformed: false })
    );
  });
});

// ===========================================================================
// PaletteBuiltinCard — visually distinct built-in card (AC9)
// ===========================================================================

describe("PaletteBuiltinCard (AC9)", () => {
  it("renders the display name and a 'built-in' tag distinguishing it from function/set cards", () => {
    render(
      React.createElement(PaletteBuiltinCard, {
        builtin: { builtin_type: "join", display_name: "Join", description: "Join two reports." },
      })
    );
    expect(screen.getByText("Join")).toBeTruthy();
    expect(screen.getByText("built-in")).toBeTruthy();
  });

  it("calls onOpenDrawer with the builtin_type when clicked (#256)", () => {
    const onOpenDrawer = vi.fn();
    render(
      React.createElement(PaletteBuiltinCard, {
        builtin: { builtin_type: "join", display_name: "Join", description: "Join two reports." },
        onOpenDrawer,
      })
    );
    fireEvent.click(screen.getByText("Join"));
    expect(onOpenDrawer).toHaveBeenCalledWith("join");
  });

  it("still fires onDragStart when dragged — the open-drawer click does not break drag (#256)", () => {
    const onDragStart = vi.fn();
    const onOpenDrawer = vi.fn();
    render(
      React.createElement(PaletteBuiltinCard, {
        builtin: { builtin_type: "join", display_name: "Join", description: "Join two reports." },
        onDragStart,
        onOpenDrawer,
      })
    );
    fireEvent.dragStart(screen.getByText("Join").closest("[draggable]"), {
      dataTransfer: { setData: () => {} },
    });
    expect(onDragStart).toHaveBeenCalled();
    expect(onOpenDrawer).not.toHaveBeenCalled();
  });
});

// ===========================================================================
// PaletteBuiltinDrawer — minimal read-only builtin detail drawer (#256)
// ===========================================================================

describe("PaletteBuiltinDrawer (#256)", () => {
  const builtin = {
    builtin_type: "join",
    display_name: "Join",
    description: "Join two reports on a key.",
    config_schema: { left_key: "string", right_key: "string" },
  };

  it("renders the builtin's name, type, description and a config summary inside the reused Drawer", () => {
    render(React.createElement(PaletteBuiltinDrawer, { builtin, onClose: () => {} }));
    const drawer = screen.getByTestId("drawer");
    expect(within(drawer).getByText("Join")).toBeTruthy();                       // display_name (title)
    expect(within(drawer).getByText("join")).toBeTruthy();                       // builtin_type tag
    expect(within(drawer).getByText("Join two reports on a key.")).toBeTruthy(); // description
    expect(within(drawer).getByText(/left_key/)).toBeTruthy();                   // config_schema summary
    expect(within(drawer).getByText(/right_key/)).toBeTruthy();
  });

  it("renders nothing when no builtin is provided", () => {
    const { container } = render(
      React.createElement(PaletteBuiltinDrawer, { builtin: null, onClose: () => {} })
    );
    expect(container.querySelector('[data-testid="drawer"]')).toBeNull();
  });
});

// ===========================================================================
// BuiltinStepCard — placed built-in step on the canvas (#209 AC3/AC5/AC6)
// ===========================================================================

function makeBuiltinStep(overrides = {}) {
  return {
    step_type: "builtin",
    step_id: "bs-1",
    builtin_type: "join",
    position: 1,
    builtin_config: {
      right_source_id: "R1",
      join_type: "inner",
      on: [{ left_col: "region", right_col: "region" }],
      keep_columns: "all",
    },
    ...overrides,
  };
}

const BUILTIN_SOURCES = [
  { source_id: "R1", source_name: "regions" },
  { source_id: "R2", source_name: "targets" },
];

describe("BuiltinStepCard (AC3 — distinct card + config summary)", () => {
  it("renders a 'built-in' tag distinguishing it from function/set step cards", () => {
    render(
      React.createElement(BuiltinStepCard, {
        step: makeBuiltinStep(), sourceId: "src-1", order: 2,
        sources: BUILTIN_SOURCES, onRemoved: () => {}, onEdit: () => {},
      })
    );
    expect(screen.getByText("built-in")).toBeTruthy();
  });

  it("shows a config summary: Join · <right source> · inner · N keys", () => {
    render(
      React.createElement(BuiltinStepCard, {
        step: makeBuiltinStep(), sourceId: "src-1", order: 2,
        sources: BUILTIN_SOURCES, onRemoved: () => {}, onEdit: () => {},
      })
    );
    // Right-source name resolved from the sources lookup; key count from on[].
    // The full summary line is a single element distinct from the card title.
    const summary = screen.getByText(/Join · regions · inner · 1 key/);
    expect(summary).toBeTruthy();
  });

  it("pluralizes the key count for a composite join", () => {
    const step = makeBuiltinStep({
      builtin_config: {
        right_source_id: "R1", join_type: "left",
        on: [{ left_col: "a", right_col: "x" }, { left_col: "b", right_col: "y" }],
        keep_columns: "all",
      },
    });
    render(
      React.createElement(BuiltinStepCard, {
        step, sourceId: "src-1", order: 2,
        sources: BUILTIN_SOURCES, onRemoved: () => {}, onEdit: () => {},
      })
    );
    expect(screen.getByText(/2 keys/)).toBeTruthy();
  });
});

describe("BuiltinStepCard (AC5 — remove)", () => {
  it("remove control calls DELETE /sources/{source_id}/attach-builtin/{step_id} then onRemoved", async () => {
    const fetchMock = vi.fn(() => Promise.resolve({ ok: true, status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const onRemoved = vi.fn();
    render(
      React.createElement(BuiltinStepCard, {
        step: makeBuiltinStep(), sourceId: "src-1", order: 2,
        sources: BUILTIN_SOURCES, onRemoved, onEdit: () => {},
      })
    );
    fireEvent.click(screen.getByTitle("Remove step"));
    expect(fetchMock).toHaveBeenCalledWith(
      "/sources/src-1/attach-builtin/bs-1",
      expect.objectContaining({ method: "DELETE" })
    );
    await waitFor(() => expect(onRemoved).toHaveBeenCalledTimes(1));
  });
});

describe("BuiltinStepCard (AC6 — edit)", () => {
  it("edit control calls onEdit with the built-in step", () => {
    const onEdit = vi.fn();
    render(
      React.createElement(BuiltinStepCard, {
        step: makeBuiltinStep(), sourceId: "src-1", order: 2,
        sources: BUILTIN_SOURCES, onRemoved: () => {}, onEdit,
      })
    );
    fireEvent.click(screen.getByTitle("Edit step"));
    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(onEdit.mock.calls[0][0].step_id).toBe("bs-1");
  });
});

// ===========================================================================
// PipelineCanvas — dispatch by step_type (#209 AC3/AC4)
// ===========================================================================

function functionStep(overrides = {}) {
  return {
    step_type: "function",
    source_function_map_id: "sfm-1",
    set_id: "set-1",
    set_name: "Set Alpha",
    position: 0,
    output_mode: "append",
    functions: [{ function_id: "fn-1", function_name: "fn_member", function_type: "transform", params: [] }],
    ...overrides,
  };
}

describe("PipelineCanvas — dispatch by step_type (AC3/AC4)", () => {
  it("renders a function step as a function card (set_name shown, no built-in tag)", () => {
    render(
      React.createElement(PipelineCanvas, {
        sourceId: "src-1", steps: [functionStep()], sources: BUILTIN_SOURCES,
        onReloadPipeline: () => {}, resultTags: {}, onNavigateResults: () => {},
        onEditStep: () => {}, onEditBuiltin: () => {},
      })
    );
    expect(screen.getByText("Set Alpha")).toBeTruthy();
    expect(screen.queryByText("built-in")).toBeNull();
  });

  it("renders a built-in step as a built-in card (built-in tag present)", () => {
    // AC4: this is what the canvas shows after the join modal save reloads the pipeline.
    render(
      React.createElement(PipelineCanvas, {
        sourceId: "src-1", steps: [makeBuiltinStep()], sources: BUILTIN_SOURCES,
        onReloadPipeline: () => {}, resultTags: {}, onNavigateResults: () => {},
        onEditStep: () => {}, onEditBuiltin: () => {},
      })
    );
    expect(screen.getByText("built-in")).toBeTruthy();
    expect(screen.getByText(/Join · regions · inner · 1 key/)).toBeTruthy();
  });

  it("treats a step with no step_type as a function card (backward safety)", () => {
    const legacy = functionStep();
    delete legacy.step_type;
    render(
      React.createElement(PipelineCanvas, {
        sourceId: "src-1", steps: [legacy], sources: BUILTIN_SOURCES,
        onReloadPipeline: () => {}, resultTags: {}, onNavigateResults: () => {},
        onEditStep: () => {}, onEditBuiltin: () => {},
      })
    );
    expect(screen.getByText("Set Alpha")).toBeTruthy();
    expect(screen.queryByText("built-in")).toBeNull();
  });

  it("renders function and built-in steps together, interleaved by array order", () => {
    render(
      React.createElement(PipelineCanvas, {
        sourceId: "src-1", steps: [functionStep(), makeBuiltinStep()], sources: BUILTIN_SOURCES,
        onReloadPipeline: () => {}, resultTags: {}, onNavigateResults: () => {},
        onEditStep: () => {}, onEditBuiltin: () => {},
      })
    );
    expect(screen.getByText("Set Alpha")).toBeTruthy();
    expect(screen.getByText("built-in")).toBeTruthy();
  });
});

// ===========================================================================
// JoinModal — edit mode pre-fill + PATCH on save (#209 AC6)
// ===========================================================================

describe("JoinModal — edit mode (AC6)", () => {
  const EDIT_CONFIG = {
    right_source_id: "R1",
    join_type: "left",
    on: [{ left_col: "region", right_col: "region" }],
    keep_columns: "all",
  };

  it("pre-fills from initialConfig: opens on step 2 with the saved join type selected", async () => {
    render(
      React.createElement(JoinModal, {
        open: true, onClose: vi.fn(),
        currentSource: LEFT_SOURCE, sources: SOURCES,
        fetchRightColumns: vi.fn(() => Promise.resolve(RIGHT_COLUMNS)),
        onSubmit: vi.fn(() => Promise.resolve({ ok: true })),
        initialConfig: EDIT_CONFIG,
      })
    );
    // Step 2 column pickers are shown immediately (skips source selection).
    await screen.findByTestId("column-list-right");
    // The saved join type 'left' button is active (Add step enabled means pairs are pre-filled)
    expect(screen.getByText("Add step").disabled).toBe(false);
  });

  it("submit in edit mode passes the edited config to onSubmit (SidePanel routes to PATCH)", async () => {
    const onSubmit = vi.fn(() => Promise.resolve({ ok: true }));
    render(
      React.createElement(JoinModal, {
        open: true, onClose: vi.fn(),
        currentSource: LEFT_SOURCE, sources: SOURCES,
        fetchRightColumns: vi.fn(() => Promise.resolve(RIGHT_COLUMNS)),
        onSubmit,
        initialConfig: EDIT_CONFIG,
      })
    );
    await screen.findByTestId("column-list-right");
    fireEvent.click(screen.getByText("Add step"));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        right_source_id: "R1",
        join_type: "left",
        on: [{ left_col: "region", right_col: "region" }],
      })
    );
  });
});

// ---------------------------------------------------------------------------
// Slice 2 (#233) — minimal reorder control for multi-column bindings
// ---------------------------------------------------------------------------

function seriesParam(overrides = {}) {
  return {
    param_id: "p-cols",
    param_name: "cols",
    param_type: "pd.Series",
    param_kind: "column",
    function_name: "fn_multi",
    suggested_columns: [],
    current_scalar_value: null,
    ...overrides,
  };
}

describe("minimal column reorder control (#233)", () => {
  it("renders a reorder control listing the selected columns in order", () => {
    // Two columns pre-selected via current_bindings, in order [amount, region]
    renderCard([
      seriesParam({
        current_bindings: [
          { column_id: "c1", column_name: "amount" },
          { column_id: "c2", column_name: "region" },
        ],
      }),
    ]);
    const list = screen.getByTestId("reorder-p-cols");
    expect(list).toBeTruthy();
    const items = within(list).getAllByTestId("reorder-item");
    expect(items).toHaveLength(2);
    // The mono column-name span carries the bound column's name in row order.
    const names = items.map(i => i.querySelector(".mono").textContent);
    expect(names).toEqual(["amount", "region"]);
  });

  it("move-down reorders the selected columns and onSave sends the new order", () => {
    const onSave = vi.fn();
    renderCard(
      [
        seriesParam({
          current_bindings: [
            { column_id: "c1", column_name: "amount" },
            { column_id: "c2", column_name: "region" },
          ],
        }),
      ],
      { onSave }
    );
    // Move "amount" (first) down so order becomes [region, amount]
    const list = screen.getByTestId("reorder-p-cols");
    const downBtns = within(list).getAllByLabelText("Move down");
    fireEvent.click(downBtns[0]);

    fireEvent.click(screen.getByText("Save"));
    expect(onSave).toHaveBeenCalledTimes(1);
    const [bindings] = onSave.mock.calls[0];
    expect(bindings).toEqual([{ param_id: "p-cols", column_ids: ["c2", "c1"] }]);
  });
});

// ---------------------------------------------------------------------------
// Slice 3 (#237) — multi-column binding + frontend equal-length-among-varying block
// ---------------------------------------------------------------------------

const FIVE_COLUMNS = [
  { column_id: "c1", column_name: "amount", column_type: "DOUBLE" },
  { column_id: "c2", column_name: "region", column_type: "VARCHAR" },
  { column_id: "c3", column_name: "qty", column_type: "INTEGER" },
  { column_id: "c4", column_name: "price", column_type: "DOUBLE" },
  { column_id: "c5", column_name: "tax", column_type: "DOUBLE" },
];

function renderCardCols(params, cols, extra = {}) {
  return render(
    React.createElement(PendingStepCard, {
      dryRunResult: { params, available_columns: cols },
      stepName: "Step",
      onSave: () => {},
      onCancel: () => {},
      saving: false,
      saveError: null,
      ...extra,
    })
  );
}

describe("PendingStepCard — multi-column binding to one parameter (#237 / slice #5)", () => {
  it("binds multiple columns to one param and onSave sends a column_ids array of length > 1", () => {
    const onSave = vi.fn();
    renderCardCols(
      [seriesParam({
        param_id: "p-cols",
        current_bindings: [
          { column_id: "c1", column_name: "amount" },
          { column_id: "c2", column_name: "region" },
        ],
      })],
      FIVE_COLUMNS,
      { onSave }
    );
    fireEvent.click(screen.getByText("Save"));
    expect(onSave).toHaveBeenCalledTimes(1);
    const [bindings] = onSave.mock.calls[0];
    expect(bindings).toEqual([{ param_id: "p-cols", column_ids: ["c1", "c2"] }]);
    expect(bindings[0].column_ids.length).toBeGreaterThan(1);
  });

  it("toggling additional columns grows the single param's binding list", () => {
    const onSave = vi.fn();
    const { container } = renderCardCols(
      [seriesParam({ param_id: "p-cols", current_bindings: [{ column_id: "c1", column_name: "amount" }] })],
      FIVE_COLUMNS,
      { onSave }
    );
    // Click the "qty" column row to add it to the same param.
    fireEvent.click(screen.getByText("qty"));
    fireEvent.click(screen.getByText("Save"));
    const [bindings] = onSave.mock.calls[0];
    expect(bindings[0].param_id).toBe("p-cols");
    expect(bindings[0].column_ids).toEqual(["c1", "c3"]);
  });
});

describe("PendingStepCard — frontend equal-length-among-varying block (#237 / slice #1)", () => {
  function twoVaryingMismatched() {
    // p-a bound to 3 columns, p-b bound to 2 — a 3,2 mismatch among varying params.
    return [
      seriesParam({
        param_id: "p-a",
        param_name: "a",
        current_bindings: [
          { column_id: "c1", column_name: "amount" },
          { column_id: "c2", column_name: "region" },
          { column_id: "c3", column_name: "qty" },
        ],
      }),
      seriesParam({
        param_id: "p-b",
        param_name: "b",
        current_bindings: [
          { column_id: "c4", column_name: "price" },
          { column_id: "c5", column_name: "tax" },
        ],
      }),
    ];
  }

  it("disables Save when two varying params bind different column counts (3,2)", () => {
    renderCardCols(twoVaryingMismatched(), FIVE_COLUMNS);
    expect(screen.getByText("Save").disabled).toBe(true);
  });

  it("shows a readable mismatch message naming the conflict", () => {
    renderCardCols(twoVaryingMismatched(), FIVE_COLUMNS);
    const msg = screen.getByTestId("equal-length-error");
    expect(msg).toBeTruthy();
    expect(msg.textContent).toMatch(/3/);
    expect(msg.textContent).toMatch(/2/);
  });

  it("allows Save when a varying param is paired with a length-1 static param (3,1)", () => {
    const params = [
      seriesParam({
        param_id: "p-a",
        param_name: "a",
        current_bindings: [
          { column_id: "c1", column_name: "amount" },
          { column_id: "c2", column_name: "region" },
          { column_id: "c3", column_name: "qty" },
        ],
      }),
      seriesParam({
        param_id: "p-b",
        param_name: "b",
        current_bindings: [{ column_id: "c4", column_name: "price" }],
      }),
    ];
    renderCardCols(params, FIVE_COLUMNS);
    expect(screen.getByText("Save").disabled).toBe(false);
    expect(screen.queryByTestId("equal-length-error")).toBeNull();
  });

  it("allows Save when two varying params bind equal counts (3,3)", () => {
    const params = [
      seriesParam({
        param_id: "p-a", param_name: "a",
        current_bindings: [
          { column_id: "c1", column_name: "amount" },
          { column_id: "c2", column_name: "region" },
          { column_id: "c3", column_name: "qty" },
        ],
      }),
      seriesParam({
        param_id: "p-b", param_name: "b",
        current_bindings: [
          { column_id: "c4", column_name: "price" },
          { column_id: "c5", column_name: "tax" },
          { column_id: "c1", column_name: "amount" },
        ],
      }),
    ];
    renderCardCols(params, FIVE_COLUMNS);
    expect(screen.getByText("Save").disabled).toBe(false);
    expect(screen.queryByTestId("equal-length-error")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Slice 4 (#241) — replace-target + append-name selection in the mapping modal
// ---------------------------------------------------------------------------

describe("PendingStepCard — replace-target + append-name (slice 4 / #241)", () => {
  function twoColParam() {
    return seriesParam({
      param_id: "p-cols",
      current_bindings: [
        { column_id: "c1", column_name: "amount" },
        { column_id: "c2", column_name: "region" },
      ],
    });
  }

  it("shows an output-mode selector defaulting to append", () => {
    renderCard([twoColParam()]);
    const sel = screen.getByTestId("output-mode-select");
    expect(sel).toBeTruthy();
    expect(sel.value).toBe("append");
  });

  it("append mode exposes an optional append-name input sent through onSave", () => {
    const onSave = vi.fn();
    renderCard([twoColParam()], { onSave });
    const nameInput = screen.getByTestId("append-name-input");
    fireEvent.change(nameInput, { target: { value: "scored" } });

    fireEvent.click(screen.getByText("Save"));
    const extras = onSave.mock.calls[0][2];
    expect(extras.output_mode).toBe("append");
    expect(extras.append_name).toBe("scored");
  });

  it("replace mode shows one ordered target picker per bound column, defaulting to the input column", () => {
    const onSave = vi.fn();
    renderCard([twoColParam()], { onSave });
    fireEvent.change(screen.getByTestId("output-mode-select"), { target: { value: "replace" } });

    const targets = screen.getByTestId("replace-targets-p-cols");
    expect(targets).toBeTruthy();
    const selects = within(targets).getAllByRole("combobox");
    // Two bound columns -> two target pickers (bundle i -> target i).
    expect(selects).toHaveLength(2);
    // Default selection is the input column at that position.
    expect(selects[0].value).toBe("c1");
    expect(selects[1].value).toBe("c2");
  });

  it("replace mode sends the chosen ordered output_targets through onSave", () => {
    const onSave = vi.fn();
    renderCard([twoColParam()], { onSave });
    fireEvent.change(screen.getByTestId("output-mode-select"), { target: { value: "replace" } });

    const targets = screen.getByTestId("replace-targets-p-cols");
    const selects = within(targets).getAllByRole("combobox");
    // Reassign bundle 0's target from amount(c1) to region(c2).
    fireEvent.change(selects[0], { target: { value: "c2" } });

    fireEvent.click(screen.getByText("Save"));
    const extras = onSave.mock.calls[0][2];
    expect(extras.output_mode).toBe("replace");
    expect(extras.output_targets).toEqual(["c2", "c2"]);
  });
});


// ===========================================================================
// FilterModal — single-step filter config (filter built-in, end-to-end)
// ===========================================================================

function renderFilter(extra = {}) {
  const onSubmit = extra.onSubmit || vi.fn(() => Promise.resolve({ ok: true, step_id: "f1" }));
  const onClose = extra.onClose || vi.fn();
  const utils = render(
    React.createElement(FilterModal, {
      open: true,
      onClose,
      currentSource: extra.currentSource || LEFT_SOURCE,
      onSubmit,
      initialConfig: extra.initialConfig || null,
    })
  );
  return { ...utils, onSubmit, onClose };
}

describe("FilterModal", () => {
  it("renders column / condition / value fields populated with the source's columns", () => {
    renderFilter();
    const col = screen.getByTestId("filter-column");
    expect(within(col).getByText("region")).toBeTruthy();
    expect(within(col).getByText("amount")).toBeTruthy();
    expect(screen.getByTestId("filter-operator")).toBeTruthy();
    expect(screen.getByTestId("filter-value")).toBeTruthy();
  });

  it("hides the value input for a nullary operator (is_null)", () => {
    renderFilter();
    fireEvent.change(screen.getByTestId("filter-operator"), { target: { value: "is_null" } });
    expect(screen.queryByTestId("filter-value")).toBeNull();
  });

  it("disables submit until a column (and a value for a binary op) are set", () => {
    renderFilter();
    expect(screen.getByText("Add step").disabled).toBe(true);
    fireEvent.change(screen.getByTestId("filter-column"), { target: { value: "amount" } });
    // operator defaults to "eq" (binary) → still needs a value
    expect(screen.getByText("Add step").disabled).toBe(true);
    fireEvent.change(screen.getByTestId("filter-value"), { target: { value: "100" } });
    expect(screen.getByText("Add step").disabled).toBe(false);
  });

  it("submits {column, operator, value} for a binary operator", async () => {
    const onSubmit = vi.fn(() => Promise.resolve({ ok: true }));
    renderFilter({ onSubmit });
    fireEvent.change(screen.getByTestId("filter-column"), { target: { value: "amount" } });
    fireEvent.change(screen.getByTestId("filter-operator"), { target: { value: "gt" } });
    fireEvent.change(screen.getByTestId("filter-value"), { target: { value: "100" } });
    fireEvent.click(screen.getByText("Add step"));
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({ column: "amount", operator: "gt", value: "100" })
    );
  });

  it("submits {column, operator} with NO value for a nullary operator", async () => {
    const onSubmit = vi.fn(() => Promise.resolve({ ok: true }));
    renderFilter({ onSubmit });
    fireEvent.change(screen.getByTestId("filter-column"), { target: { value: "region" } });
    fireEvent.change(screen.getByTestId("filter-operator"), { target: { value: "is_null" } });
    fireEvent.click(screen.getByText("Add step"));
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({ column: "region", operator: "is_null" })
    );
  });

  it("pre-fills from initialConfig in edit mode and labels the action 'Save filter' (Principle 7)", () => {
    renderFilter({ initialConfig: { column: "amount", operator: "lte", value: "50" } });
    expect(screen.getByTestId("filter-column").value).toBe("amount");
    expect(screen.getByTestId("filter-operator").value).toBe("lte");
    expect(screen.getByTestId("filter-value").value).toBe("50");
    expect(screen.getByText("Save filter")).toBeTruthy();
  });
});


// ===========================================================================
// RenameModal — column→new-name pairs (rename built-in, end-to-end)
// ===========================================================================

function renderRename(extra = {}) {
  const onSubmit = extra.onSubmit || vi.fn(() => Promise.resolve({ ok: true, step_id: "r1" }));
  const onClose = extra.onClose || vi.fn();
  const utils = render(
    React.createElement(RenameModal, {
      open: true,
      onClose,
      currentSource: extra.currentSource || LEFT_SOURCE,
      onSubmit,
      initialConfig: extra.initialConfig || null,
    })
  );
  return { ...utils, onSubmit, onClose };
}

describe("RenameModal", () => {
  it("renders one from→to pair initially", () => {
    renderRename();
    expect(screen.getByTestId("rename-from-0")).toBeTruthy();
    expect(screen.getByTestId("rename-to-0")).toBeTruthy();
  });

  it("disables submit until at least one complete pair", () => {
    renderRename();
    expect(screen.getByText("Add step").disabled).toBe(true);
    fireEvent.change(screen.getByTestId("rename-from-0"), { target: { value: "amount" } });
    expect(screen.getByText("Add step").disabled).toBe(true);  // no new name yet
    fireEvent.change(screen.getByTestId("rename-to-0"), { target: { value: "total" } });
    expect(screen.getByText("Add step").disabled).toBe(false);
  });

  it("submits {renames: {from: to}} for a single pair", async () => {
    const onSubmit = vi.fn(() => Promise.resolve({ ok: true }));
    renderRename({ onSubmit });
    fireEvent.change(screen.getByTestId("rename-from-0"), { target: { value: "amount" } });
    fireEvent.change(screen.getByTestId("rename-to-0"), { target: { value: "total" } });
    fireEvent.click(screen.getByText("Add step"));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({ renames: { amount: "total" } }));
  });

  it("supports multiple pairs via Add another", async () => {
    const onSubmit = vi.fn(() => Promise.resolve({ ok: true }));
    renderRename({ onSubmit });
    fireEvent.change(screen.getByTestId("rename-from-0"), { target: { value: "amount" } });
    fireEvent.change(screen.getByTestId("rename-to-0"), { target: { value: "total" } });
    fireEvent.click(screen.getByText("+ Add another"));
    fireEvent.change(screen.getByTestId("rename-from-1"), { target: { value: "region" } });
    fireEvent.change(screen.getByTestId("rename-to-1"), { target: { value: "area" } });
    fireEvent.click(screen.getByText("Add step"));
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({ renames: { amount: "total", region: "area" } })
    );
  });

  it("blocks submit when two columns map to the same new name", () => {
    renderRename();
    fireEvent.change(screen.getByTestId("rename-from-0"), { target: { value: "amount" } });
    fireEvent.change(screen.getByTestId("rename-to-0"), { target: { value: "x" } });
    fireEvent.click(screen.getByText("+ Add another"));
    fireEvent.change(screen.getByTestId("rename-from-1"), { target: { value: "region" } });
    fireEvent.change(screen.getByTestId("rename-to-1"), { target: { value: "x" } });
    expect(screen.getByText("Add step").disabled).toBe(true);
    expect(screen.getByText(/same new name/)).toBeTruthy();
  });

  it("pre-fills saved pairs in order on edit, labelled 'Save rename' (Principle 7)", () => {
    renderRename({ initialConfig: { renames: { amount: "total", region: "area" } } });
    expect(screen.getByTestId("rename-from-0").value).toBe("amount");
    expect(screen.getByTestId("rename-to-0").value).toBe("total");
    expect(screen.getByTestId("rename-from-1").value).toBe("region");
    expect(screen.getByTestId("rename-to-1").value).toBe("area");
    expect(screen.getByText("Save rename")).toBeTruthy();
  });
});
