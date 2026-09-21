import { describe, expect, it } from "vitest";
import { mergeProps, renderTree } from "./App";
import { buildTree, partitionSidebar } from "./layout";
import { recorder, render } from "../src/test-utils";
import type { UiNode } from "./types";

const n = (
  kind: string,
  id: string,
  props: Record<string, unknown> = {},
): UiNode => ({
  id,
  kind,
  props,
});

/** Render a flat spec the way `App` does, so these cover the real pipeline. */
function mount(nodes: UiNode[], values: Record<string, unknown> = {}) {
  const { calls, send } = recorder();
  const { main } = partitionSidebar(buildTree(nodes));
  const r = render(<>{renderTree(main, send, values)}</>);
  return { ...r, calls };
}

describe("columns", () => {
  const spec = [
    n("columns_start", "cols", { widths: [3, 2] }),
    n("column_start", "c1"),
    n("number", "n1", { label: "Left" }),
    n("column_end", "ce1"),
    n("column_start", "c2"),
    n("number", "n2", { label: "Right" }),
    n("column_end", "ce2"),
    n("columns_end", "colse"),
  ];

  it("emits one grid cell per column, in order", () => {
    const ui = mount(spec);
    const columns = ui.all(".kin-column");
    expect(columns).toHaveLength(2);
    expect(columns[0].textContent).toContain("Left");
    expect(columns[1].textContent).toContain("Right");
  });

  it("turns declared widths into fr tracks", () => {
    const grid = mount(spec).find(".kin-columns") as HTMLElement;
    expect(grid.style.gridTemplateColumns).toBe(
      "minmax(0, 3fr) minmax(0, 2fr)",
    );
  });

  it("falls back to equal tracks when widths are absent", () => {
    const grid = mount([
      n("columns_start", "cols"),
      n("column_start", "c1"),
      n("column_end", "ce1"),
      n("column_start", "c2"),
      n("column_end", "ce2"),
      n("columns_end", "colse"),
    ]).find(".kin-columns") as HTMLElement;
    expect(grid.style.gridTemplateColumns).toBe(
      "minmax(0, 1fr) minmax(0, 1fr)",
    );
  });

  it("keeps an empty column as a cell", () => {
    const ui = mount([
      n("columns_start", "cols", { widths: [1, 1] }),
      n("column_start", "c1"),
      n("number", "n1", { label: "Only" }),
      n("column_end", "ce1"),
      n("column_start", "c2"),
      n("column_end", "ce2"),
      n("columns_end", "colse"),
    ]);
    expect(ui.all(".kin-column")).toHaveLength(2);
  });
});

describe("tabs", () => {
  const spec = [
    n("tabs_start", "tabs", { labels: ["Inputs", "Advanced"] }),
    n("tab_start", "t1", { label: "Inputs" }),
    n("number", "n1", { label: "Seed" }),
    n("tab_end", "te1"),
    n("tab_start", "t2", { label: "Advanced" }),
    n("checkbox", "cb1", { label: "Override" }),
    n("tab_end", "te2"),
    n("tabs_end", "tabse"),
  ];

  it("renders a tab per panel with the first active", () => {
    const ui = mount(spec);
    expect(ui.all(".kin-tab").map((t) => t.textContent)).toEqual([
      "Inputs",
      "Advanced",
    ]);
    expect(ui.all(".kin-tab.active").map((t) => t.textContent)).toEqual([
      "Inputs",
    ]);
  });

  it("switches the active panel on click", () => {
    const ui = mount(spec);
    ui.click(ui.all(".kin-tab")[1]);
    expect(ui.all(".kin-tab.active").map((t) => t.textContent)).toEqual([
      "Advanced",
    ]);
    expect(ui.all('[role="tabpanel"]').map((p) => p.className)).toEqual([
      "kin-tabpanel hidden",
      "kin-tabpanel",
    ]);
  });

  // Unmounting a hidden panel would reset its widgets and drop rendered results.
  it("keeps hidden panels mounted", () => {
    const ui = mount(spec);
    expect(ui.text()).toContain("Override");
    expect(ui.all('[role="tabpanel"]')).toHaveLength(2);
  });
});

describe("group", () => {
  const spec = [
    n("group_start", "g1", { title: "Fitting" }),
    n("checkbox", "cb1", { label: "Override" }),
    n("group_end", "ge1"),
  ];

  it("collapses and expands without unmounting its children", () => {
    const ui = mount(spec);
    expect(ui.find(".kin-group-body")?.className).toBe("kin-group-body");
    ui.click(ui.find(".kin-group-head"));
    expect(ui.find(".kin-group-body")?.className).toBe(
      "kin-group-body collapsed",
    );
    expect(ui.text()).toContain("Override"); // still mounted, just hidden
    ui.click(ui.find(".kin-group-head"));
    expect(ui.find(".kin-group-body")?.className).toBe("kin-group-body");
  });

  it("does not toggle when collapsible is false", () => {
    const ui = mount([
      n("group_start", "g1", { title: "Fixed", collapsible: false }),
      n("checkbox", "cb1", { label: "x" }),
      n("group_end", "ge1"),
    ]);
    ui.click(ui.find(".kin-group-head"));
    expect(ui.find(".kin-group-body")?.className).toBe("kin-group-body");
  });
});

describe("enabled_when", () => {
  const spec = [
    n("checkbox", "gate", { label: "Advanced", value: false }),
    n("number", "n1", { label: "Iterations", enabled_when: "gate" }),
  ];

  it("greys out the dependant while its controller is falsy", () => {
    const ui = mount(spec, { gate: false });
    const blocks = ui.all(".block");
    expect(blocks[0].className).toBe("block");
    expect(blocks[1].className).toBe("block disabled");
  });

  it("enables it once the controller is truthy", () => {
    const ui = mount(spec, { gate: true });
    expect(ui.all(".block")[1].className).toBe("block");
  });

  // The greyed-out block is also non-interactive: its events must not reach the runtime.
  it("swallows events from a disabled widget", () => {
    const ui = mount(spec, { gate: false });
    ui.type(ui.all("input")[1], "9");
    expect(ui.calls).toHaveLength(0);
  });

  it("lets events through once enabled", () => {
    const ui = mount(spec, { gate: true });
    ui.type(ui.all("input")[1], "9");
    expect(ui.calls.map((c) => c.id)).toEqual(["n1"]);
  });
});

describe("mergeProps", () => {
  const node = () => n("job_status", "s1", { status: "running", logs: ["a"] });

  it("shallow-merges plain props", () => {
    const out = mergeProps(node(), { status: "completed", progress: 100 });
    expect(out.props).toEqual({
      status: "completed",
      progress: 100,
      logs: ["a"],
    });
  });

  it("appends logs_append to logs without storing the key", () => {
    const out = mergeProps(node(), { logs_append: ["b", "c"] });
    expect(out.props.logs).toEqual(["a", "b", "c"]);
    expect("logs_append" in out.props).toBe(false);
  });

  it("starts logs from the appended lines when none exist", () => {
    const out = mergeProps(n("job_status", "s1"), { logs_append: ["first"] });
    expect(out.props.logs).toEqual(["first"]);
  });

  it("resets logs when the patch carries a fresh array", () => {
    const out = mergeProps(node(), { status: "running", logs: [] });
    expect(out.props.logs).toEqual([]);
  });

  it("does not mutate the original node", () => {
    const original = node();
    mergeProps(original, { logs_append: ["b"] });
    expect(original.props.logs).toEqual(["a"]);
  });
});

describe("unknown kinds", () => {
  it("renders a placeholder rather than blanking the page", () => {
    const ui = mount([n("text", "t1", { value: "kept" }), n("nonesuch", "x1")]);
    expect(ui.text()).toContain("kept");
    expect(ui.text()).toContain("unknown widget");
  });
});
