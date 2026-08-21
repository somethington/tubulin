import { useState } from "react";
import type { WidgetProps } from "../types";

export function NumberInput({ node, send }: WidgetProps) {
  const [v, setV] = useState<number>(node.props.value ?? 0);
  return (
    <div className="kin-input">
      <label>{node.props.label}</label>
      <input
        type="number"
        value={v}
        onChange={(e) => {
          const nv = e.target.value === "" ? 0 : Number(e.target.value);
          setV(nv);
          send(node.id, "change", { value: nv });
        }}
      />
    </div>
  );
}
