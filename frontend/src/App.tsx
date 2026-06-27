import { useEffect, useRef, useState } from "react";
import { KinesinSocket } from "./websocket";
import type { ServerMessage, Send, UiNode } from "./types";
import { Text } from "./widgets/Text";
import { Button } from "./widgets/Button";
import { FileUpload } from "./widgets/FileUpload";
import { JobStatus } from "./widgets/JobStatus";
import { TablePreview } from "./widgets/TablePreview";
import { Checkbox } from "./widgets/Checkbox";
import { Slider } from "./widgets/Slider";
import { NumberInput } from "./widgets/NumberInput";
import { Select } from "./widgets/Select";
import { ImagePlot } from "./widgets/ImagePlot";
import { PlotlyChart } from "./widgets/PlotlyChart";
import { Download } from "./widgets/Download";
import { Gallery } from "./widgets/Gallery";

function Widget({ node, send }: { node: UiNode; send: Send }) {
  switch (node.kind) {
    case "text":
      return <Text node={node} send={send} />;
    case "header":
      return <h2 className="kin-header">{node.props.value}</h2>;
    case "button":
      return <Button node={node} send={send} />;
    case "file_upload":
      return <FileUpload node={node} send={send} />;
    case "job_status":
      return <JobStatus node={node} send={send} />;
    case "table_preview":
      return <TablePreview node={node} send={send} />;
    case "checkbox":
      return <Checkbox node={node} send={send} />;
    case "slider":
      return <Slider node={node} send={send} />;
    case "number":
      return <NumberInput node={node} send={send} />;
    case "select":
      return <Select node={node} send={send} />;
    case "image":
      return <ImagePlot node={node} send={send} />;
    case "plotly":
      return <PlotlyChart node={node} send={send} />;
    case "download":
      return <Download node={node} send={send} />;
    case "gallery":
      return <Gallery node={node} send={send} />;
    default:
      return (
        <div className="muted">
          unknown widget: <code>{node.kind}</code>
        </div>
      );
  }
}

const noop: Send = () => {};

// A collapsible card grouping the widgets between its start marker and matching end.
function Group({
  node,
  items,
  send,
  values,
}: {
  node: UiNode;
  items: UiNode[];
  send: Send;
  values: Record<string, any>;
}) {
  const collapsible = node.props.collapsible !== false;
  const [open, setOpen] = useState<boolean>(node.props.expanded !== false);
  return (
    <section className="kin-group">
      <button
        type="button"
        className="kin-group-head"
        onClick={() => collapsible && setOpen((o) => !o)}
        disabled={!collapsible}
      >
        <span className="kin-group-title">{node.props.title}</span>
        {collapsible && (
          <span className={`kin-group-chev ${open ? "open" : ""}`} aria-hidden>
            ⌄
          </span>
        )}
      </button>
      {/* Keep children mounted when collapsed (hide via CSS) so their state and
          values aren't lost on collapse → expand. */}
      <div className={`kin-group-body${open ? "" : " collapsed"}`}>
        {renderNodes(items, send, values)}
      </div>
    </section>
  );
}

// Flat node list → blocks, collapsing `group_start`…`group_end` spans into <Group>.
// `values` holds current input values so a widget with `enabled_when` can disable
// itself when its controller is falsy.
function renderNodes(
  nodes: UiNode[],
  send: Send,
  values: Record<string, any>,
): any[] {
  const out: any[] = [];
  let i = 0;
  while (i < nodes.length) {
    const n = nodes[i];
    if (n.kind === "group_start") {
      const items: UiNode[] = [];
      let depth = 1;
      i++;
      while (i < nodes.length && depth > 0) {
        if (nodes[i].kind === "group_start") depth++;
        else if (nodes[i].kind === "group_end") {
          depth--;
          if (depth === 0) {
            i++;
            break;
          }
        }
        items.push(nodes[i]);
        i++;
      }
      out.push(
        <Group key={n.id} node={n} items={items} send={send} values={values} />,
      );
    } else if (n.kind === "group_end") {
      i++; // stray end marker — skip
    } else {
      const gate = n.props.enabled_when as string | undefined;
      const disabled = gate ? !values[gate] : false;
      out.push(
        <div className={`block${disabled ? " disabled" : ""}`} key={n.id}>
          <Widget node={n} send={disabled ? noop : send} />
        </div>,
      );
      i++;
    }
  }
  return out;
}

export function App() {
  const [title, setTitle] = useState("TPP Solver");
  const [nodes, setNodes] = useState<UiNode[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Current input values, so widgets with `enabled_when` can react to their controller.
  const [values, setValues] = useState<Record<string, any>>({});
  const [theme, setTheme] = useState<"light" | "dark">(
    () => (localStorage.getItem("kinesin-theme") as "light" | "dark") || "light",
  );
  const sockRef = useRef<KinesinSocket | null>(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("kinesin-theme", theme);
  }, [theme]);

  useEffect(() => {
    const sock = new KinesinSocket(
      handleMessage,
      () => setConnected(true),
      () => setConnected(false),
    );
    sockRef.current = sock;
    return () => sock.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleMessage(msg: ServerMessage) {
    if (msg.type === "ui_tree") {
      setTitle(msg.title);
      setNodes(msg.nodes);
      const seed: Record<string, any> = {};
      for (const n of msg.nodes) {
        if (n.props && "value" in n.props) seed[n.id] = n.props.value;
      }
      setValues(seed);
    } else if (msg.type === "widget_update") {
      setNodes((prev) =>
        prev.map((n) =>
          n.id === msg.node_id
            ? { ...n, props: { ...n.props, ...msg.props } }
            : n,
        ),
      );
    } else if (msg.type === "error") {
      setError(msg.message);
    }
  }

  const send: Send = (nodeId, event, payload = {}) => {
    // Mirror input changes into local state so `enabled_when` dependents react.
    if (event === "change" && payload && "value" in payload) {
      setValues((v) => ({ ...v, [nodeId]: (payload as any).value }));
    }
    sockRef.current?.send({ type: "event", node_id: nodeId, event, payload });
  };

  return (
    <div className="app">
      <aside className="sidebar">
        <img className="logo" src="/logo.svg" alt="logo" />
        <div className="side-title">Navigation</div>
        <nav className="side-nav">
          <a className="active">Main App</a>
          <a>GO Annotation</a>
          <a>README</a>
        </nav>
        <div className="side-foot">
          <span className={`dot ${connected ? "ok" : "off"}`} />
          {connected ? "connected" : "offline"} · Kinesin runtime
          <button
            className="theme-toggle"
            onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
          >
            {theme === "dark" ? "☀ light" : "☾ dark"}
          </button>
        </div>
      </aside>

      <main className="main">
        <h1 className="page-title">{title}</h1>

        {error && (
          <div className="banner" onClick={() => setError(null)}>
            {error} <span className="muted">(click to dismiss)</span>
          </div>
        )}

        {nodes.length === 0 ? (
          <div className="muted">Waiting for the runtime…</div>
        ) : (
          renderNodes(nodes, send, values)
        )}
      </main>
    </div>
  );
}
