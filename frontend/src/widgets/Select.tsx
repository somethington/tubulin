import { useState } from "react";
import type { WidgetProps } from "../types";

export function Select({ node, send }: WidgetProps) {
  const { label, options } = node.props;
  const [v, setV] = useState<string>(node.props.value);
  return (
    <div className="kin-input">
      <label>{label}</label>
      <select
        value={v}
        onChange={(e) => {
          setV(e.target.value);
          send(node.id, "change", { value: e.target.value });
        }}
      >
        {(options ?? []).map((o: string) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </div>
  );
}
