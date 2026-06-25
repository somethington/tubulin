import { useEffect, useState } from "react";
import type { WidgetProps } from "../types";

interface PreviewData {
  columns?: string[];
  rows?: any[][];
  total_rows?: number;
  truncated?: boolean;
  error?: string;
}

export function TablePreview({ node }: WidgetProps) {
  const props = node.props;
  const [fetched, setFetched] = useState<PreviewData | null>(null);

  // Static-path case: a table bound to a fixed file fetches its own preview.
  // Slot-bound tables instead receive columns/rows via widget_update.
  useEffect(() => {
    const hasInline = (props.columns?.length ?? 0) > 0;
    if (!hasInline && props.path) {
      fetch(`/preview?path=${encodeURIComponent(props.path)}`)
        .then((r) => r.json())
        .then(setFetched)
        .catch((e) => setFetched({ error: String(e) }));
    }
  }, [props.path, props.columns]);

  const data: PreviewData = fetched ?? props;
  const caption = props.caption as string | undefined;

  if (data.error) {
    return <div className="kin-table err">preview error: {data.error}</div>;
  }
  if (props.missing) {
    return (
      <div className="kin-table muted">
        {caption && <div className="kin-caption">{caption}</div>}
        Not generated — enable the option and re-run.
      </div>
    );
  }
  if (!data.columns || data.columns.length === 0) {
    return (
      <div className="kin-table muted">
        {caption && <div className="kin-caption">{caption}</div>}
        No result yet.
      </div>
    );
  }

  return (
    <div className="kin-table">
      {caption && <div className="kin-caption">{caption}</div>}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {data.columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(data.rows ?? []).map((row, i) => (
              <tr key={i}>
                {row.map((v, j) => (
                  <td key={j}>{v === null ? "∅" : String(v)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.total_rows != null && (
        <div className="muted small">
          {data.truncated
            ? `showing first ${data.rows?.length ?? 0} of ${data.total_rows} rows`
            : `${data.total_rows} rows`}
        </div>
      )}
    </div>
  );
}
