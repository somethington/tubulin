import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { TextInput } from "./TextInput";
import { TextArea } from "./TextArea";
import { node, recorder, render } from "../test-utils";
import type { UiNode } from "../types";

const idle = (ms = 300) => act(() => void vi.advanceTimersByTime(ms));

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("TextInput", () => {
  const mount = (props: Record<string, unknown> = {}) => {
    const { calls, send, last } = recorder();
    const r = render(
      <TextInput
        node={node("text_input", { label: "Run label", ...props }) as UiNode}
        send={send}
      />,
    );
    return { ...r, calls, last, input: () => r.find("input") };
  };

  it("seeds from the spec default and renders the placeholder", () => {
    const ui = mount({ value: "run-1", placeholder: "e.g. HeLa" });
    expect((ui.input() as HTMLInputElement).value).toBe("run-1");
    expect((ui.input() as HTMLInputElement).placeholder).toBe("e.g. HeLa");
  });

  // Keystrokes are local; only the settled value becomes a session write.
  it("sends one change after the idle window, not one per keystroke", () => {
    const ui = mount({ value: "" });
    ui.type(ui.input(), "H");
    ui.type(ui.input(), "He");
    ui.type(ui.input(), "HeLa");
    expect(ui.calls).toHaveLength(0);
    idle();
    expect(ui.calls).toHaveLength(1);
    expect(ui.calls[0]).toMatchObject({
      id: "text_input_1",
      event: "change",
      payload: { value: "HeLa" },
    });
  });

  it("shows every keystroke locally while the send is pending", () => {
    const ui = mount({ value: "" });
    ui.type(ui.input(), "par");
    expect((ui.input() as HTMLInputElement).value).toBe("par");
    expect(ui.calls).toHaveLength(0);
  });

  it("flushes immediately on blur", () => {
    const ui = mount({ value: "" });
    ui.type(ui.input(), "done");
    ui.blur(ui.input());
    expect(ui.calls).toHaveLength(1);
    expect(ui.calls[0].payload).toEqual({ value: "done" });
  });

  it("does not re-send an unchanged value", () => {
    const ui = mount({ value: "" });
    ui.type(ui.input(), "x");
    idle();
    ui.blur(ui.input());
    idle();
    expect(ui.calls).toHaveLength(1);
  });

  // Otherwise a value typed and immediately navigated away from is silently lost.
  it("flushes a pending edit on unmount", () => {
    const ui = mount({ value: "" });
    ui.type(ui.input(), "late");
    expect(ui.calls).toHaveLength(0);
    ui.unmount();
    expect(ui.calls).toHaveLength(1);
    expect(ui.calls[0].payload).toEqual({ value: "late" });
  });

  it("sends an empty string when the field is cleared", () => {
    const ui = mount({ value: "seed" });
    ui.type(ui.input(), "");
    idle();
    expect(ui.last()?.payload).toEqual({ value: "" });
  });
});

describe("TextArea", () => {
  const mount = (props: Record<string, unknown> = {}) => {
    const { calls, send, last } = recorder();
    const r = render(
      <TextArea
        node={node("text_area", { label: "Notes", ...props }) as UiNode}
        send={send}
      />,
    );
    return { ...r, calls, last, area: () => r.find("textarea") };
  };

  it("honours the row count from the spec", () => {
    expect(mount({ rows: 6 }).area()?.getAttribute("rows")).toBe("6");
  });

  it("debounces multi-line input the same way", () => {
    const ui = mount({ value: "" });
    ui.type(ui.area(), "line one\nline two");
    expect(ui.calls).toHaveLength(0);
    idle();
    expect(ui.last()?.payload).toEqual({ value: "line one\nline two" });
  });
});
