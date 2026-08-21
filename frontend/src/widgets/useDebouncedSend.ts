import { useCallback, useEffect, useRef, useState } from "react";
import type { Send } from "../types";

/** Idle window before a free-text edit becomes a session write. */
const IDLE_MS = 300;

/**
 * Local-first text state: keystrokes stay in the component and one `change` event
 * is sent after a short idle (or immediately, via `flush`, on blur / Enter) — so a
 * session-state write doesn't ride every character.
 */
export function useDebouncedSend(nodeId: string, send: Send, initial: string) {
  const [value, setValue] = useState<string>(initial);
  const timer = useRef<number | undefined>(undefined);
  const pending = useRef<string>(initial);
  const sent = useRef<string>(initial);

  const flush = useCallback(() => {
    window.clearTimeout(timer.current);
    if (pending.current === sent.current) return;
    sent.current = pending.current;
    send(nodeId, "change", { value: pending.current });
  }, [nodeId, send]);

  const onChange = useCallback(
    (next: string) => {
      setValue(next);
      pending.current = next;
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(flush, IDLE_MS);
    },
    [flush],
  );

  // A pending edit would otherwise be dropped when the widget unmounts.
  useEffect(() => () => flush(), [flush]);

  return { value, onChange, flush };
}
