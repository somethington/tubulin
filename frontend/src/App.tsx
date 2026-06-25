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

export function App() {
  const [title, setTitle] = useState("TPP Solver");
  const [nodes, setNodes] = useState<UiNode[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sockRef = useRef<KinesinSocket | null>(null);

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

  const send: Send = (nodeId, event, payload = {}) =>
    sockRef.current?.send({ type: "event", node_id: nodeId, event, payload });

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
          nodes.map((n) => (
            <div className="block" key={n.id}>
              <Widget node={n} send={send} />
            </div>
          ))
        )}
      </main>
    </div>
  );
}
