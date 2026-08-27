import { useState } from "react";
import type { WidgetProps } from "../types";

export function Slider({ node, send }: WidgetProps) {
  const { label, min, max, step } = node.props;
  const [v, setV] = useState<number>(node.props.value ?? node.props.min ?? 0);
  return (
    <div className="kin-input">
      <label>
        {label}: <strong>{v}</strong>
      </label>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={v}
        onChange={(e) => {
          const nv = parseFloat(e.target.value);
          setV(nv);
          send(node.id, "change", { value: nv });
        }}
      />
    </div>
  );
}
