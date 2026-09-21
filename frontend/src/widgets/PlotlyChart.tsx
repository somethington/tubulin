import { useEffect, useRef } from "react";
import Plotly from "plotly.js-dist-min";
// @ts-expect-error — plain-JS theme from the @tubulin/design package
import { tubulinPlotlyLayout } from "@tubulin/design/plotly-theme.js";
import type { WidgetProps } from "../types";

export function PlotlyChart({ node }: WidgetProps) {
  const ref = useRef<HTMLDivElement>(null);
  const { ready, figure, caption, missing, error } = node.props;

  useEffect(() => {
    if (ref.current && figure?.data) {
      // Apply the Tubulin chart chrome (bg / fonts / grid / colorway) while keeping
      // the figure's own titles, axis labels and ranges.
      const dark =
        document.documentElement.getAttribute("data-theme") === "dark";
      const theme = tubulinPlotlyLayout(dark ? "dark" : "light");
      const fig = figure.layout ?? {};
      const layout = {
        ...theme,
        ...fig,
        paper_bgcolor: theme.paper_bgcolor,
        plot_bgcolor: theme.plot_bgcolor,
        colorway: theme.colorway,
        font: theme.font,
        xaxis: { ...theme.xaxis, ...(fig.xaxis ?? {}) },
        yaxis: { ...theme.yaxis, ...(fig.yaxis ?? {}) },
      };
      Plotly.react(ref.current, figure.data, layout, {
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
        <div className="muted small">
          Not generated — enable the option and re-run.
        </div>
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
