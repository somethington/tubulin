import { act } from "react";
import type { ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";

// Tells React that act() is driving updates, so it doesn't warn about them.
(
  globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

/** Mount a component into a detached container and drive it synchronously. */
export function render(ui: ReactNode) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  let root!: Root;
  act(() => {
    root = createRoot(container);
    root.render(ui);
  });

  return {
    container,
    html: () => container.innerHTML,
    text: () => container.textContent ?? "",
    find: (selector: string) => container.querySelector(selector),
    all: (selector: string) => Array.from(container.querySelectorAll(selector)),
    click: (el: Element | null) => {
      if (!el) throw new Error("click: element not found");
      act(() => {
        el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
    },
    /** Type into a text input/textarea the way React's onChange expects. */
    type: (el: Element | null, value: string) => {
      if (!el) throw new Error("type: element not found");
      act(() => {
        setNativeValue(el as HTMLInputElement | HTMLTextAreaElement, value);
        el.dispatchEvent(new Event("input", { bubbles: true }));
      });
    },
    // React delegates at the root, and `onBlur` is wired to the *bubbling*
    // `focusout` event — a plain non-bubbling `blur` would never reach it.
    blur: (el: Element | null) => {
      if (!el) throw new Error("blur: element not found");
      act(() => {
        el.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
      });
    },
    unmount: () => act(() => root.unmount()),
  };
}

// React tracks the last value it wrote on the DOM node; assigning `.value` directly
// would be swallowed as a no-op change, so go through the prototype setter.
function setNativeValue(
  el: HTMLInputElement | HTMLTextAreaElement,
  value: string,
): void {
  const proto =
    el.tagName === "TEXTAREA"
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  if (!setter) throw new Error("no native value setter");
  setter.call(el, value);
}

/** Build a `UiNode` for a widget under test. */
export function node(kind: string, props: Record<string, unknown> = {}) {
  return { id: `${kind}_1`, kind, props };
}

/** A `send` spy recording every (nodeId, event, payload) it receives. */
export function recorder() {
  const calls: Array<{ id: string; event: string; payload: unknown }> = [];
  const send = (id: string, event: string, payload?: unknown) => {
    calls.push({ id, event, payload });
  };
  const last = () => calls[calls.length - 1];
  return { calls, send, last };
}
