import { useState } from "react";
import type { WidgetProps } from "../types";

export function FileUpload({ node, send }: WidgetProps) {
  const [status, setStatus] = useState("");
  const [filename, setFilename] = useState("");

  async function onChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setFilename(file.name);
    setStatus("uploading…");

    const form = new FormData();
    form.append("file", file);
    try {
      const res = await fetch("/upload", { method: "POST", body: form });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setStatus(`uploaded → ${data.path}`);
      // Tell the runtime which session this upload belongs to.
      send(node.id, "upload", { path: data.path, filename: data.filename });
    } catch (err: any) {
      setStatus(`error: ${err.message ?? err}`);
    }
  }

  return (
    <div className="kin-upload">
      <label>{node.props.label ?? "Upload"}</label>
      <input type="file" onChange={onChange} />
      {filename && (
        <div className="muted small">
          {filename} — {status}
        </div>
      )}
    </div>
  );
}
