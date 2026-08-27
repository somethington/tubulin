import type { WidgetProps } from "../types";
import { useDebouncedSend } from "./useDebouncedSend";

export function TextArea({ node, send }: WidgetProps) {
  const { value, onChange, flush } = useDebouncedSend(
    node.id,
    send,
    node.props.value ?? "",
  );
  return (
    <div className="kin-input">
      <label>{node.props.label}</label>
      <textarea
        value={value}
        rows={node.props.rows ?? 4}
        placeholder={node.props.placeholder ?? ""}
        onChange={(e) => onChange(e.target.value)}
        onBlur={flush}
      />
    </div>
  );
}
