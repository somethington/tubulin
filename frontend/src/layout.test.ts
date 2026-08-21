import { describe, expect, it } from "vitest";
import { buildTree, partitionSidebar, CONTAINERS, type TreeNode } from "./layout";
import type { UiNode } from "./types";

const n = (kind: string, id = kind): UiNode => ({ id, kind, props: {} });

/** Compact "kind(child, child)" rendering of a tree, for readable assertions. */
const shape = (tree: TreeNode[]): string =>
  tree
    .map((t) => (t.children.length ? `${t.node.kind}(${shape(t.children)})` : t.node.kind))
    .join(",");

describe("buildTree", () => {
  it("leaves a flat list flat", () => {
    expect(shape(buildTree([n("text"), n("button"), n("slider")]))).toBe(
      "text,button,slider",
    );
  });

  it("nests a balanced container", () => {
    const tree = buildTree([
      n("group_start"),
      n("checkbox"),
      n("group_end"),
      n("button"),
    ]);
    expect(shape(tree)).toBe("group_start(checkbox),button");
  });

  it("nests containers inside containers", () => {
    const tree = buildTree([
      n("tabs_start"),
      n("tab_start", "t1"),
      n("columns_start"),
      n("column_start", "c1"),
      n("number"),
      n("column_end", "ce1"),
      n("column_start", "c2"),
      n("slider"),
      n("column_end", "ce2"),
      n("columns_end"),
      n("tab_end", "te1"),
      n("tabs_end"),
    ]);
    expect(shape(tree)).toBe(
      "tabs_start(tab_start(columns_start(column_start(number),column_start(slider))))",
    );
  });

  it("keeps sibling containers separate", () => {
    const tree = buildTree([
      n("group_start", "g1"),
      n("text", "a"),
      n("group_end", "ge1"),
      n("group_start", "g2"),
      n("text", "b"),
      n("group_end", "ge2"),
    ]);
    expect(tree).toHaveLength(2);
    expect(tree.every((t) => t.children.length === 1)).toBe(true);
  });

  // Malformed spans must degrade, never blank the page.
  it("drops a stray closing marker", () => {
    expect(shape(buildTree([n("group_end"), n("text"), n("tab_end")]))).toBe("text");
  });

  it("lets an unclosed container run to the end of the list", () => {
    expect(shape(buildTree([n("group_start"), n("text"), n("button")]))).toBe(
      "group_start(text,button)",
    );
  });

  it("ignores a closer that does not match the open container", () => {
    // `tab_end` cannot close a `group_start`, so the group stays open.
    expect(shape(buildTree([n("group_start"), n("text"), n("tab_end"), n("button")]))).toBe(
      "group_start(text,button)",
    );
  });

  it("returns nothing for an empty spec", () => {
    expect(buildTree([])).toEqual([]);
  });
});

describe("partitionSidebar", () => {
  it("hoists sidebar children out and leaves the rest in order", () => {
    const { sidebar, main } = partitionSidebar(
      buildTree([
        n("sidebar_start"),
        n("select"),
        n("multiselect"),
        n("sidebar_end"),
        n("markdown"),
        n("button"),
      ]),
    );
    expect(shape(sidebar)).toBe("select,multiselect");
    expect(shape(main)).toBe("markdown,button");
  });

  it("concatenates several sidebar blocks", () => {
    const { sidebar, main } = partitionSidebar(
      buildTree([
        n("sidebar_start", "s1"),
        n("select"),
        n("sidebar_end", "se1"),
        n("text"),
        n("sidebar_start", "s2"),
        n("slider"),
        n("sidebar_end", "se2"),
      ]),
    );
    expect(shape(sidebar)).toBe("select,slider");
    expect(shape(main)).toBe("text");
  });

  it("yields an empty sidebar when the app declares none", () => {
    const { sidebar, main } = partitionSidebar(buildTree([n("text"), n("button")]));
    expect(sidebar).toEqual([]);
    expect(shape(main)).toBe("text,button");
  });
});

describe("CONTAINERS", () => {
  // The Python side emits `<kind>_start` / `<kind>_end`; a mismatch here means a
  // container renders as an "unknown widget" row instead of nesting.
  it("pairs every opener with its own _end marker", () => {
    for (const [open, close] of Object.entries(CONTAINERS)) {
      expect(open.endsWith("_start")).toBe(true);
      expect(close).toBe(open.replace(/_start$/, "_end"));
    }
  });
});
