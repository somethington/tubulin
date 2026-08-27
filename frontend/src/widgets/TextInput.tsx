import type { WidgetProps } from "../types";
import { useDebouncedSend } from "./useDebouncedSend";

export function TextInput({ node, send }: WidgetProps) {
  const { value, onChange, flush } = useDebouncedSend(
    node.id,
    send,
    node.props.value ?? "",
  );
  return (
    <div className="kin-input">
      <label>{node.props.label}</label>
      <input
        type="text"
        value={value}
        placeholder={node.props.placeholder ?? ""}
        onChange={(e) => onChange(e.target.value)}
        onBlur={flush}
        onKeyDown={(e) => e.key === "Enter" && flush()}
      />
    </div>
  );
}
