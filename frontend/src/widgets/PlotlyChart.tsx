import { useEffect, useRef } from "react";
import Plotly from "plotly.js-dist-min";
import type { WidgetProps } from "../types";

export function PlotlyChart({ node }: WidgetProps) {
  const ref = useRef<HTMLDivElement>(null);
  const { ready, figure, caption, missing, error } = node.props;

  useEffect(() => {
    if (ref.current && figure?.data) {
      Plotly.react(ref.current, figure.data, figure.layout ?? {}, {
        responsive: true,
        displayModeBar: true,
        displaylogo: false,
      });
    }
  }, [figure]);

  return (
    <div className="kin-image">
      {caption && <div className="kin-caption">{caption}</div>}
      {!ready && <div className="muted small">Waiting for plot…</div>}
      {ready && missing && (
        <div className="muted small">Not generated — enable the option and re-run.</div>
      )}
      {ready && error && <div className="kin-table err">{error}</div>}
      <div
        ref={ref}
        className="kin-plotly"
        style={{ display: ready && figure ? "block" : "none" }}
      />
    </div>
  );
}
