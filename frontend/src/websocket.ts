import type { EventMessage, ServerMessage } from "./types";

/** Thin wrapper around the runtime WebSocket at /ws. */
export class TubulinSocket {
  private ws: WebSocket;

  constructor(
    onMessage: (m: ServerMessage) => void,
    onOpen?: () => void,
    onClose?: () => void,
  ) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(`${proto}://${location.host}/ws`);
    this.ws.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data) as ServerMessage);
      } catch (err) {
        console.error("bad message from runtime", err);
      }
    };
    this.ws.onopen = () => onOpen?.();
    this.ws.onclose = () => onClose?.();
  }

  send(msg: EventMessage): void {
    if (this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  close(): void {
    this.ws.close();
  }
}
