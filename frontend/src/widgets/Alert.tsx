import type { WidgetProps } from "../types";
import { blocks } from "./Markdown";

const LEVELS = ["info", "success", "warning", "error"] as const;
type Level = (typeof LEVELS)[number];

// Announce problems assertively; guidance politely. A warning that steals focus from
// a running job is worse than one the reader reaches in their own time.
const ROLE: Record<Level, { role: string; live: "polite" | "assertive" }> = {
  info: { role: "status", live: "polite" },
  success: { role: "status", live: "polite" },
  warning: { role: "status", live: "polite" },
  error: { role: "alert", live: "assertive" },
};

const ICON: Record<Level, string> = {
  info: "i",
  success: "✓",
  warning: "!",
  error: "×",
};

/** A coloured callout. The body goes through the `app.markdown` renderer. */
export function Alert({ node }: WidgetProps) {
  const raw = String(node.props.level ?? "info");
  const level: Level = (LEVELS as readonly string[]).includes(raw)
    ? (raw as Level)
    : "info";
  const title = String(node.props.title ?? "");
  const { role, live } = ROLE[level];
  return (
    <div
      className={`kin-alert kin-alert-${level}`}
      role={role}
      aria-live={live}
    >
      <span className="kin-alert-icon" aria-hidden>
        {ICON[level]}
      </span>
      <div className="kin-alert-body">
        {title && <div className="kin-alert-title">{title}</div>}
        <div className="kin-md">{blocks(String(node.props.value ?? ""))}</div>
      </div>
    </div>
  );
}
