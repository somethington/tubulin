import type { ReactNode } from "react";
import type { UiNode } from "../types";
import type { TreeNode } from "../layout";

/**
 * Side-by-side regions. `widths` are relative (`fr`) units, so `[2, 1]` is a
 * two-thirds / one-third split. Below `STACK_AT` the columns stack — a narrow window
 * should scroll, never squeeze a slider into 40px.
 */
export function Columns({
  node,
  items,
  render,
}: {
  node: UiNode;
  items: TreeNode[];
  render: (items: TreeNode[]) => ReactNode;
}) {
  const widths: number[] = node.props.widths ?? [];
  const columns = items.filter((c) => c.node.kind === "column_start");
  return (
    <div
      className="kin-columns"
      style={{
        gridTemplateColumns: (widths.length ? widths : columns.map(() => 1))
          .map((w) => `minmax(0, ${w}fr)`)
          .join(" "),
      }}
    >
      {columns.map((column) => (
        <div className="kin-column" key={column.node.id}>
          {render(column.children)}
        </div>
      ))}
    </div>
  );
}
