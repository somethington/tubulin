import type { WidgetProps } from "../types";

export function ImagePlot({ node }: WidgetProps) {
  const { ready, svg, caption, missing, error } = node.props;
  return (
    <div className="kin-image">
      {caption && <div className="kin-caption">{caption}</div>}
      {!ready && <div className="muted small">Waiting for plot…</div>}
      {ready && missing && (
        <div className="muted small">Not generated — enable the option and re-run.</div>
      )}
      {ready && error && <div className="kin-table err">{error}</div>}
      {ready && svg && (
        <div className="kin-svg" dangerouslySetInnerHTML={{ __html: svg }} />
      )}
    </div>
  );
}
