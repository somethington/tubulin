import { useState } from "react";
import type { WidgetProps } from "../types";

export function Checkbox({ node, send }: WidgetProps) {
  const [checked, setChecked] = useState<boolean>(node.props.value ?? false);
  return (
    <label className="kin-input kin-checkbox">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => {
          setChecked(e.target.checked);
          send(node.id, "change", { value: e.target.checked });
        }}
      />
      <span>{node.props.label}</span>
    </label>
  );
}
