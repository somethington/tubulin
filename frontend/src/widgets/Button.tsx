import type { WidgetProps } from "../types";

export function Button({ node, send }: WidgetProps) {
  return (
    <button className="kin-button" onClick={() => send(node.id, "click")}>
      {node.props.label ?? "Button"}
    </button>
  );
}
