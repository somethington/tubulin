import { useMemo, useState } from "react";
import type { WidgetProps } from "../types";

interface Item {
  protein: string;
  path: string;
}

const PER_PAGE = 9;

export function Gallery({ node }: WidgetProps) {
  const { ready, caption, missing, error } = node.props;
  const items: Item[] = node.props.items ?? [];
  const [page, setPage] = useState(0);
  const [q, setQ] = useState("");

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return needle
      ? items.filter((it) => it.protein.toLowerCase().includes(needle))
      : items;
  }, [items, q]);

  const pages = Math.max(1, Math.ceil(filtered.length / PER_PAGE));
  const safePage = Math.min(page, pages - 1);
  const slice = filtered.slice(
    safePage * PER_PAGE,
    safePage * PER_PAGE + PER_PAGE,
  );

  return (
    <div className="kin-gallery">
      {caption && <div className="kin-caption">{caption}</div>}
      {!ready && <div className="muted small">Waiting for curves…</div>}
      {ready && missing && (
        <div className="muted small">
          Not generated — enable the option and re-run.
        </div>
      )}
      {ready && error && <div className="kin-table err">{error}</div>}

      {ready && !missing && !error && (
        <>
          <div className="gallery-bar">
            <input
              className="gallery-search"
              type="text"
              placeholder="Filter by protein…"
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setPage(0);
              }}
            />
            <span className="muted small">{filtered.length} curves</span>
          </div>

          <div className="gallery-grid">
            {slice.map((it) => (
              <figure className="gallery-cell" key={it.protein}>
                <img
                  loading="lazy"
                  src={`/file?path=${encodeURIComponent(it.path)}`}
                  alt={it.protein}
                />
                <figcaption>{it.protein}</figcaption>
              </figure>
            ))}
          </div>

          {pages > 1 && (
            <div className="gallery-pager">
              <button
                className="kin-button"
                disabled={safePage === 0}
                onClick={() => setPage(safePage - 1)}
              >
                ‹ Prev
              </button>
              <span className="muted small">
                Page {safePage + 1} / {pages}
              </span>
              <button
                className="kin-button"
                disabled={safePage >= pages - 1}
                onClick={() => setPage(safePage + 1)}
              >
                Next ›
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
