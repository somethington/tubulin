import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { KinesinSocket } from "./websocket";
import type { ServerMessage, Send, UiNode } from "./types";
import { buildTree, partitionSidebar, type TreeNode } from "./layout";
import { Text } from "./widgets/Text";
import { Markdown } from "./widgets/Markdown";
import { Button } from "./widgets/Button";
import { FileUpload } from "./widgets/FileUpload";
import { JobStatus } from "./widgets/JobStatus";
import { TablePreview } from "./widgets/TablePreview";
import { Checkbox } from "./widgets/Checkbox";
import { Slider } from "./widgets/Slider";
import { NumberInput } from "./widgets/NumberInput";
import { Select } from "./widgets/Select";
import { MultiSelect } from "./widgets/MultiSelect";
import { TextInput } from "./widgets/TextInput";
import { TextArea } from "./widgets/TextArea";
import { ImagePlot } from "./widgets/ImagePlot";
import { PlotlyChart } from "./widgets/PlotlyChart";
import { Download } from "./widgets/Download";
import { Gallery } from "./widgets/Gallery";
import { Columns } from "./widgets/Columns";
import { Tabs } from "./widgets/Tabs";

function Widget({ node, send }: { node: UiNode; send: Send }) {
  switch (node.kind) {
    case "text":
      return <Text node={node} send={send} />;
    case "markdown":
      return <Markdown node={node} send={send} />;
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
    case "multiselect":
      return <MultiSelect node={node} send={send} />;
    case "text_input":
      return <TextInput node={node} send={send} />;
    case "text_area":
      return <TextArea node={node} send={send} />;
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
  render,
}: {
  node: UiNode;
  items: TreeNode[];
  render: (items: TreeNode[]) => ReactNode;
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
        {render(items)}
      </div>
    </section>
  );
}

/**
 * Render a container tree (see `layout.ts`). `values` holds current input values so a
 * widget with `enabled_when` can disable itself when its controller is falsy.
 */
export function renderTree(
  items: TreeNode[],
  send: Send,
  values: Record<string, any>,
): ReactNode[] {
  const render = (kids: TreeNode[]) => renderTree(kids, send, values);

  return items.map(({ node, children }) => {
    switch (node.kind) {
      case "group_start":
        return (
          <Group key={node.id} node={node} items={children} render={render} />
        );
      case "columns_start":
        return (
          <Columns key={node.id} node={node} items={children} render={render} />
        );
      case "tabs_start":
        return (
          <Tabs key={node.id} node={node} items={children} render={render} />
        );
      default: {
        const gate = node.props.enabled_when as string | undefined;
        const disabled = gate ? !values[gate] : false;
        return (
          <div className={`block${disabled ? " disabled" : ""}`} key={node.id}>
            <Widget node={node} send={disabled ? noop : send} />
          </div>
        );
      }
    }
  });
}

export function App() {
  const [title, setTitle] = useState("Kinesin");
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
    document.title = title;
  }, [title]);

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

  const { sidebar, main } = useMemo(
    () => partitionSidebar(buildTree(nodes)),
    [nodes],
  );

  return (
    <div className="app">
      <aside className="sidebar">
        <img className="logo" src="/logo.svg" alt="logo" />
        {sidebar.length > 0 && (
          <div className="side-body">{renderTree(sidebar, send, values)}</div>
        )}
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
          renderTree(main, send, values)
        )}
      </main>
    </div>
  );
}
