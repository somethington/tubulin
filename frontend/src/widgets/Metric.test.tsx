import { describe, expect, it } from "vitest";
import { Metric, deltaDirection, formatValue } from "./Metric";
import { node, render } from "../test-utils";
import type { UiNode } from "../types";

const mount = (props: Record<string, unknown>) => {
  const r = render(
    <Metric node={node("metric", props) as UiNode} send={() => {}} />,
  );
  return {
    ...r,
    value: () => r.find(".kin-metric-value")?.textContent,
    delta: () => r.find(".kin-metric-delta"),
  };
};

describe("formatValue", () => {
  it("separates thousands and caps runaway float precision", () => {
    expect(formatValue(1234567)).toBe("1,234,567");
    // 0.1 + 0.2 must not become the headline number.
    expect(formatValue(0.1 + 0.2)).toBe("0.3");
  });

  it("passes strings through and shows a dash for nothing", () => {
    expect(formatValue("n/a")).toBe("n/a");
    expect(formatValue(null)).toBe("—");
    expect(formatValue(undefined)).toBe("—");
    expect(formatValue("")).toBe("—");
  });

  it("does not mangle non-finite numbers", () => {
    expect(formatValue(NaN)).toBe("NaN");
    expect(formatValue(Infinity)).toBe("Infinity");
  });
});

describe("deltaDirection", () => {
  it("signs numbers and signed strings alike", () => {
    expect(deltaDirection(4)).toBe(1);
    expect(deltaDirection(-4)).toBe(-1);
    expect(deltaDirection(0)).toBe(0);
    expect(deltaDirection("+12")).toBe(1);
    expect(deltaDirection("-3.1%")).toBe(-1);
    expect(deltaDirection("2 fewer")).toBe(1);
    expect(deltaDirection("steady")).toBe(0);
    expect(deltaDirection(null)).toBe(0);
  });
});

describe("Metric", () => {
  it("renders a static value immediately", () => {
    expect(mount({ label: "Replicates", value: 3, ready: true }).value()).toBe(
      "3",
    );
  });

  // A bound tile has no number until its job finishes; it must not claim one.
  it("holds a placeholder until the job reports", () => {
    const ui = mount({ label: "Fitted", value: null, ready: false });
    expect(ui.value()).toBe("—");
    // Nothing to report yet, and no reserved row either — the tile is a placeholder.
    expect(ui.delta()).toBeNull();
  });

  it("marks a delta up or down", () => {
    expect(
      mount({ label: "n", value: 10, delta: 2, ready: true }).delta()
        ?.className,
    ).toContain("up");
    expect(
      mount({ label: "n", value: 10, delta: -2, ready: true }).delta()
        ?.className,
    ).toContain("down");
  });

  // Lower is better for an error rate: a drop should read as good.
  it("flips the colouring for delta_color=inverse", () => {
    const ui = mount({
      label: "Error rate",
      value: 0.02,
      delta: -0.01,
      delta_color: "inverse",
      ready: true,
    });
    expect(ui.delta()?.className).toContain("up");
  });

  it("renders delta_color=off unstyled and without an arrow", () => {
    const ui = mount({
      label: "n",
      value: 10,
      delta: 5,
      delta_color: "off",
      ready: true,
    });
    expect(ui.delta()?.className).toContain("flat");
    expect(ui.delta()?.textContent).toBe("5");
  });

  // Reserved, not omitted: a KPI row where only one tile has a delta must stay level.
  it("reserves an empty delta row when there is no delta", () => {
    const ui = mount({ label: "n", value: 1, delta: null, ready: true });
    expect(ui.delta()).not.toBeNull();
    expect(ui.delta()?.textContent).toBe("");
  });

  it("surfaces a runtime error instead of a number", () => {
    const ui = mount({ label: "n", ready: true, error: "bad metric json" });
    expect(ui.text()).toContain("bad metric json");
    expect(ui.value()).toBeUndefined();
  });

  it("shows a help affordance only when help text is given", () => {
    expect(
      mount({ label: "n", value: 1, ready: true }).find(".kin-metric-help"),
    ).toBeNull();
    const ui = mount({
      label: "n",
      value: 1,
      ready: true,
      help: "per condition",
    });
    expect(ui.find(".kin-metric-label")?.getAttribute("title")).toBe(
      "per condition",
    );
  });
});
