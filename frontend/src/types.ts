// Wire types mirroring crates/tubulin-protocol/src/lib.rs. Keep in sync.

export interface UiNode {
  id: string;
  kind: string;
  props: Record<string, any>;
}

export type ServerMessage =
  | { type: "ui_tree"; session_id: string; title: string; nodes: UiNode[] }
  | { type: "widget_update"; node_id: string; props: Record<string, any> }
  | { type: "error"; message: string };

export interface EventMessage {
  type: "event";
  node_id: string;
  event: string;
  payload: any;
}

/** Send a browser event to the runtime. */
export type Send = (nodeId: string, event: string, payload?: any) => void;

export interface WidgetProps {
  node: UiNode;
  send: Send;
}
