import type { UiNode } from "./types";

/**
 * Container markers: the AppSpec is a **flat** node list (so every widget's value
 * seeds and flows identically regardless of nesting), and layout is expressed as
 * balanced `<kind>_start` / `<kind>_end` marker pairs around a span of it. This maps
 * each opening marker to the marker that closes it.
 */
export const CONTAINERS: Record<string, string> = {
  group_start: "group_end",
  columns_start: "columns_end",
  column_start: "column_end",
  tabs_start: "tabs_end",
  tab_start: "tab_end",
  sidebar_start: "sidebar_end",
};

const CLOSERS = new Set(Object.values(CONTAINERS));

export interface TreeNode {
  node: UiNode;
  children: TreeNode[];
}

/**
 * Flat marker list → nested tree. Unbalanced input is tolerated rather than thrown:
 * a stray closer is dropped, and containers left open at the end simply extend to
 * the end of the list — a malformed span should never blank the page.
 */
export function buildTree(nodes: UiNode[]): TreeNode[] {
  const roots: TreeNode[] = [];
  const stack: TreeNode[] = [];
  const current = () =>
    stack.length ? stack[stack.length - 1].children : roots;

  for (const node of nodes) {
    if (node.kind in CONTAINERS) {
      const branch: TreeNode = { node, children: [] };
      current().push(branch);
      stack.push(branch);
    } else if (CLOSERS.has(node.kind)) {
      const open = stack[stack.length - 1];
      if (open && CONTAINERS[open.node.kind] === node.kind) stack.pop();
    } else {
      current().push({ node, children: [] });
    }
  }
  return roots;
}

/** Split the top level into declared sidebar spans and everything else. */
export function partitionSidebar(tree: TreeNode[]): {
  sidebar: TreeNode[];
  main: TreeNode[];
} {
  const sidebar: TreeNode[] = [];
  const main: TreeNode[] = [];
  for (const branch of tree) {
    if (branch.node.kind === "sidebar_start") sidebar.push(...branch.children);
    else main.push(branch);
  }
  return { sidebar, main };
}
