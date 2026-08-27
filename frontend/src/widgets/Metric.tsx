import type { WidgetProps } from "../types";

/** Format a metric for display without inventing precision.
 *
 * Numbers get thousands separators; a float keeps whatever the job computed, capped
 * at 6 fraction digits so a float64 artefact (`0.30000000000000004`) doesn't become
 * the headline. Strings pass through — a job that wants "1.2 kB" or "n/a" says so.
 */
export function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return String(value);
    return value.toLocaleString(undefined, { maximumFractionDigits: 6 });
  }
  return String(value);
}

/** Which way a delta points: 1 up, -1 down, 0 flat/unknown. */
export function deltaDirection(delta: unknown): number {
  if (delta === null || delta === undefined || delta === "") return 0;
  if (typeof delta === "number") return Math.sign(delta);
  // A string delta ("+12", "-3.1%", "2 fewer", "1.2× faster") is signed by its
  // leading number. Match the prefix rather than stripping non-digits: stripping
  // turns "2 fewer" into "2ee" (the e's survive as exponent characters) -> NaN.
  const text = String(delta).trim().replace(/^\u2212/, "-");
  const [head] = text.match(/^[+-]?\d*\.?\d+/) ?? [];
  if (head === undefined) return 0;
  if (head.startsWith("+")) return 1;
  return Math.sign(Number(head)) || (head.startsWith("-") ? -1 : 0);
}

export function Metric({ node }: WidgetProps) {
  const { label, value, delta, help, delta_color, ready, missing, error } =
    node.props;

  const direction = deltaDirection(delta);
  // "inverse" is for metrics where lower is better (error rate, runtime).
  const good = delta_color === "inverse" ? direction < 0 : direction > 0;
  const tone =
    delta_color === "off" || direction === 0 ? "flat" : good ? "up" : "down";

  const body = () => {
    if (error) return <div className="kin-metric-error">{String(error)}</div>;
    if (!ready) return <div className="kin-metric-value muted">—</div>;
    if (missing)
      return (
        <div className="kin-metric-value muted" title="the job produced no value">
          —
        </div>
      );
    const hasDelta = delta !== null && delta !== undefined && delta !== "";
    return (
      <>
        <div className="kin-metric-value">{formatValue(value)}</div>
        {/* The delta row is always present, even when empty: tiles sit in a grid
            that top-aligns its cells, so a row where only one metric has a delta
            would step down by a line and read as a misalignment. Reserving the
            line keeps a KPI row level without the shared column layout having to
            know that metrics are special. */}
        <div className={`kin-metric-delta ${tone}`}>
          {hasDelta && direction !== 0 && delta_color !== "off" && (
            <span aria-hidden>{direction > 0 ? "▲" : "▼"}</span>
          )}
          {hasDelta && <span>{formatValue(delta)}</span>}
        </div>
      </>
    );
  };

  return (
    <div className="kin-metric">
      <div className="kin-metric-label" title={help || undefined}>
        {label}
        {help && (
          <span className="kin-metric-help" aria-hidden>
            ?
          </span>
        )}
      </div>
      {body()}
    </div>
  );
}
