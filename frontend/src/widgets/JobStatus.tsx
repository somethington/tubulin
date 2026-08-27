import type { WidgetProps } from "../types";

export function JobStatus({ node }: WidgetProps) {
  const status: string = node.props.status ?? "idle";
  const progress: number = node.props.progress ?? 0;
  const message: string = node.props.message ?? "";
  const logs: string[] = node.props.logs ?? [];
  const elapsed = node.props.elapsed_s as number | undefined;

  return (
    <div className={`kin-status status-${status}`}>
      <div className="status-head">
        <strong>Job status</strong>
        <span className="status-right">
          {elapsed != null && <span className="elapsed">⏱ {elapsed}s total</span>}
          <span className={`pill pill-${status}`}>{status}</span>
        </span>
      </div>
      <div className="bar">
        <div className="bar-fill" style={{ width: `${progress}%` }} />
      </div>
      {message && <div className="status-msg">{message}</div>}
      {logs.length > 0 && <pre className="logs">{logs.join("\n")}</pre>}
    </div>
  );
}
