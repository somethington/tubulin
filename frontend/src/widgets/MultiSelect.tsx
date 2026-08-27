import { useMemo, useState } from "react";
import type { WidgetProps } from "../types";

/** Above this many options the list gets a filter box. */
const FILTER_THRESHOLD = 8;

/**
 * Multiple choice over a fixed option list. A native `<select multiple>` is hostile
 * (ctrl-click to keep a selection), so this is a scrollable checkbox list; the value
 * that reaches the job is the selected options **in declaration order**, so a job can
 * rely on the order regardless of how the user clicked.
 */
export function MultiSelect({ node, send }: WidgetProps) {
  const options: string[] = node.props.options ?? [];
  const [selected, setSelected] = useState<string[]>(node.props.value ?? []);
  const [filter, setFilter] = useState("");

  const chosen = useMemo(() => new Set(selected), [selected]);

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q ? options.filter((o) => o.toLowerCase().includes(q)) : options;
  }, [options, filter]);

  function commit(next: Set<string>) {
    const ordered = options.filter((o) => next.has(o));
    setSelected(ordered);
    send(node.id, "change", { value: ordered });
  }

  const toggle = (option: string) => {
    const next = new Set(chosen);
    if (next.has(option)) next.delete(option);
    else next.add(option);
    commit(next);
  };

  return (
    <div className="kin-input kin-multiselect">
      <label>
        {node.props.label}
        <span className="kin-ms-count">
          {selected.length} of {options.length}
        </span>
      </label>

      <div className="kin-ms-bar">
        <button type="button" onClick={() => commit(new Set(visible))}>
          {filter ? "select shown" : "select all"}
        </button>
        <button type="button" onClick={() => commit(new Set())}>
          clear
        </button>
        {options.length > FILTER_THRESHOLD && (
          <input
            className="kin-ms-filter"
            type="text"
            placeholder="filter…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        )}
      </div>

      <div className="kin-ms-list">
        {visible.length === 0 && <div className="muted small">no matches</div>}
        {visible.map((o) => (
          <label key={o} className="kin-ms-item">
            <input
              type="checkbox"
              checked={chosen.has(o)}
              onChange={() => toggle(o)}
            />
            <span>{o}</span>
          </label>
        ))}
      </div>
    </div>
  );
}
