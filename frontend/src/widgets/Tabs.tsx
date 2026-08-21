import { useState } from "react";
import type { ReactNode } from "react";
import type { UiNode } from "../types";
import type { TreeNode } from "../layout";

/**
 * Tabbed regions. Inactive panels stay **mounted** (hidden via CSS) for the same
 * reason a collapsed `app.group` does: switching tabs must not reset a widget's
 * local state or drop an in-flight job's rendered output.
 */
export function Tabs({
  node,
  items,
  render,
}: {
  node: UiNode;
  items: TreeNode[];
  render: (items: TreeNode[]) => ReactNode;
}) {
  const panels = items.filter((c) => c.node.kind === "tab_start");
  const labels: string[] = node.props.labels ?? panels.map((p) => p.node.props.label);
  const [active, setActive] = useState(0);

  return (
    <section className="kin-tabs">
      <div className="kin-tablist" role="tablist">
        {panels.map((panel, i) => (
          <button
            key={panel.node.id}
            type="button"
            role="tab"
            aria-selected={i === active}
            className={`kin-tab${i === active ? " active" : ""}`}
            onClick={() => setActive(i)}
          >
            {labels[i] ?? panel.node.props.label ?? `Tab ${i + 1}`}
          </button>
        ))}
      </div>
      {panels.map((panel, i) => (
        <div
          key={panel.node.id}
          role="tabpanel"
          className={`kin-tabpanel${i === active ? "" : " hidden"}`}
        >
          {render(panel.children)}
        </div>
      ))}
    </section>
  );
}
