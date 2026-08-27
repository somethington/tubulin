import type { ReactNode } from "react";
import type { WidgetProps } from "../types";

// A deliberately small CommonMark subset rendered straight to React nodes: no HTML
// string, no sanitizer, and no markdown dependency (the framework's dependency floor
// is a standing concern — see roadmap #16). Supported: ATX headings, fenced code,
// unordered/ordered lists, blockquotes, horizontal rules, paragraphs, and the inline
// run of `code`, **bold**, *italic*, ~~strike~~ and [links](url).

const BULLET = /^\s*[-*+]\s+/;
const ORDERED = /^\s*\d+[.)]\s+/;
const BLOCK_START = /^\s*(#{1,6}\s|>|```)/;

// Kept as a source string, not a shared `RegExp`: `inline` recurses into link text,
// and a module-level /g regex would have its `lastIndex` reset by the inner call,
// restarting the outer scan forever.
const INLINE_SRC =
  "`([^`]+)`|\\*\\*([^*]+)\\*\\*|__([^_]+)__|~~([^~]+)~~|\\*([^*]+)\\*|_([^_]+)_|\\[([^\\]]*)\\]\\(([^)\\s]+)\\)";

/**
 * Relative links and fragments pass through; an *absolute* URL is only allowed for
 * schemes that can't execute script, so `javascript:` / `data:` degrade to a dead
 * link. (Markdown here comes from the app's own Python file, which already has code
 * execution — this is hygiene, not a trust boundary.)
 */
function safeHref(href: string): string {
  const t = href.trim();
  const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(t);
  if (!scheme) return t; // relative path, fragment, or protocol-relative
  return /^(https?|mailto|ftp)$/i.test(scheme[1]) ? t : "#";
}

function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  const scanner = new RegExp(INLINE_SRC, "g");
  let last = 0;
  let reached = 0;
  let n = 0;
  let m: RegExpExecArray | null;
  while ((m = scanner.exec(text)) !== null) {
    // Progress guard. `scanner` is per-call precisely so the recursion below can't
    // rewind it, but a scanner that ever stopped advancing would spin forever and
    // freeze the tab; stop instead. A truncated render beats a hung page.
    if (scanner.lastIndex <= reached) break;
    reached = scanner.lastIndex;
    if (m.index > last) out.push(text.slice(last, m.index));
    const key = `${keyBase}-${n++}`;
    if (m[1] !== undefined)
      out.push(
        <code key={key} className="kin-md-code">
          {m[1]}
        </code>,
      );
    else if (m[2] !== undefined) out.push(<strong key={key}>{m[2]}</strong>);
    else if (m[3] !== undefined) out.push(<strong key={key}>{m[3]}</strong>);
    else if (m[4] !== undefined) out.push(<del key={key}>{m[4]}</del>);
    else if (m[5] !== undefined) out.push(<em key={key}>{m[5]}</em>);
    else if (m[6] !== undefined) out.push(<em key={key}>{m[6]}</em>);
    else
      out.push(
        <a
          key={key}
          href={safeHref(m[8])}
          target="_blank"
          rel="noreferrer noopener"
        >
          {inline(m[7], key)}
        </a>,
      );
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Render the markdown subset as React nodes. Shared with the Alert widget, so a
 *  callout body formats exactly like `app.markdown` text. */
export function blocks(src: string): ReactNode[] {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  let k = 0;

  // Every branch below consumes at least one line, so a well-formed scan runs at most
  // one iteration per line. The budget is a backstop: if a future branch ever fails to
  // advance, this ends the scan instead of freezing the tab in an infinite loop.
  let budget = lines.length * 4 + 64;

  while (i < lines.length) {
    if (budget-- <= 0) {
      out.push(
        <p key={`overflow-${k}`} className="kin-md-p">
          {lines.slice(i).join(" ")}
        </p>,
      );
      break;
    }
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }

    const fence = /^\s*```(\S*)\s*$/.exec(line);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i++]);
      i++; // closing fence, or EOF
      out.push(
        <pre key={k++} className="kin-md-pre">
          <code>{body.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      const Tag = `h${heading[1].length}` as "h1";
      out.push(
        <Tag key={k} className="kin-md-h">
          {inline(heading[2].trim(), `h${k++}`)}
        </Tag>,
      );
      i++;
      continue;
    }

    // Before the list check: `---` and `- - -` are rules, not bullets.
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      out.push(<hr key={k++} className="kin-md-hr" />);
      i++;
      continue;
    }

    if (/^\s*>/.test(line)) {
      const body: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i]))
        body.push(lines[i++].replace(/^\s*>\s?/, ""));
      out.push(
        <blockquote key={k++} className="kin-md-quote">
          {blocks(body.join("\n"))}
        </blockquote>,
      );
      continue;
    }

    if (BULLET.test(line) || ORDERED.test(line)) {
      const ordered = ORDERED.test(line);
      const marker = ordered ? ORDERED : BULLET;
      const items: string[] = [];
      while (i < lines.length && marker.test(lines[i])) {
        let item = lines[i++].replace(marker, "");
        // Lazy continuation: an indented, non-marker line belongs to the item above.
        while (
          i < lines.length &&
          lines[i].trim() &&
          !BULLET.test(lines[i]) &&
          !ORDERED.test(lines[i])
        )
          item += " " + lines[i++].trim();
        items.push(item);
      }
      const kids = items.map((t, n) => <li key={n}>{inline(t, `li${k}-${n}`)}</li>);
      out.push(
        ordered ? (
          <ol key={k++} className="kin-md-list">
            {kids}
          </ol>
        ) : (
          <ul key={k++} className="kin-md-list">
            {kids}
          </ul>
        ),
      );
      continue;
    }

    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !BLOCK_START.test(lines[i]) &&
      !BULLET.test(lines[i]) &&
      !ORDERED.test(lines[i])
    )
      para.push(lines[i++].trim());
    // A line that looks like a block opener but matched no branch above (an unclosed
    // fence with a spaced info string, say) would consume nothing and spin here
    // forever. Take it as literal prose so the scanner always advances.
    if (para.length === 0) para.push(lines[i++].trim());
    out.push(
      <p key={k} className="kin-md-p">
        {inline(para.join(" "), `p${k++}`)}
      </p>,
    );
  }
  return out;
}

export function Markdown({ node }: WidgetProps) {
  return <div className="kin-md">{blocks(String(node.props.value ?? ""))}</div>;
}
