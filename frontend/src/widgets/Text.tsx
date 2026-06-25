import type { WidgetProps } from "../types";

export function Text({ node }: WidgetProps) {
  return <p className="kin-text">{node.props.value}</p>;
}
