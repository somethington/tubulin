import type { WidgetProps } from "../types";

export function Download({ node }: WidgetProps) {
  const { ready, url, filename, label, missing } = node.props;
  if (!ready)
    return (
      <div className="muted small">Run the analysis to enable download.</div>
    );
  if (missing || !url)
    return <div className="muted small">No download available.</div>;
  return (
    <a className="kin-button kin-download" href={url} download={filename}>
      {label ?? "Download"}
    </a>
  );
}
