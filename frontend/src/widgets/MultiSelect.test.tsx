import { describe, expect, it } from "vitest";
import { MultiSelect } from "./MultiSelect";
import { node, recorder, render } from "../test-utils";
import type { UiNode } from "../types";

const OPTIONS = ["37C", "42C", "47C", "52C", "57C", "62C", "67C", "72C", "77C"];

const mount = (props: Record<string, unknown>) => {
  const { calls, send, last: lastCall } = recorder();
  const r = render(
    <MultiSelect node={node("multiselect", props) as UiNode} send={send} />,
  );
  const boxes = () => r.all('.kin-ms-item input[type="checkbox"]');
  const labels = () => r.all(".kin-ms-item span").map((e) => e.textContent);
  const last = () => lastCall()?.payload as { value: string[] } | undefined;
  return { ...r, calls, boxes, labels, last };
};

describe("MultiSelect", () => {
  it("renders one row per option and checks the defaults", () => {
    const ui = mount({ label: "Conditions", options: OPTIONS, value: ["42C"] });
    expect(ui.labels()).toEqual(OPTIONS);
    expect(ui.boxes().map((b) => (b as HTMLInputElement).checked)).toEqual([
      false, true, false, false, false, false, false, false, false,
    ]);
  });

  // The job indexes into this list, so the order must come from the spec, not the
  // order the user happened to click.
  it("emits the selection in declaration order, not click order", () => {
    const ui = mount({ label: "Conditions", options: OPTIONS, value: [] });
    ui.click(ui.boxes()[2]); // 47C
    ui.click(ui.boxes()[0]); // 37C
    ui.click(ui.boxes()[1]); // 42C
    expect(ui.last()?.value).toEqual(["37C", "42C", "47C"]);
  });

  it("toggles an option off again", () => {
    const ui = mount({ label: "C", options: OPTIONS, value: ["37C", "42C"] });
    ui.click(ui.boxes()[0]);
    expect(ui.last()?.value).toEqual(["42C"]);
  });

  it("selects all and clears", () => {
    const ui = mount({ label: "C", options: OPTIONS, value: [] });
    ui.click(ui.all(".kin-ms-bar button")[0]);
    expect(ui.last()?.value).toEqual(OPTIONS);
    ui.click(ui.all(".kin-ms-bar button")[1]);
    expect(ui.last()?.value).toEqual([]);
  });

  it("shows the selected count", () => {
    const ui = mount({ label: "C", options: OPTIONS, value: ["37C", "47C"] });
    expect(ui.find(".kin-ms-count")?.textContent).toBe("2 of 9");
  });

  it("offers a filter only once the list is long", () => {
    expect(mount({ label: "C", options: ["a", "b"], value: [] }).find(".kin-ms-filter")).toBeNull();
    expect(mount({ label: "C", options: OPTIONS, value: [] }).find(".kin-ms-filter")).not.toBeNull();
  });

  it("filters the visible rows and narrows 'select shown' to them", () => {
    const ui = mount({ label: "C", options: OPTIONS, value: [] });
    ui.type(ui.find(".kin-ms-filter"), "7");
    const matching = ["37C", "47C", "57C", "67C", "72C", "77C"];
    expect(ui.labels()).toEqual(matching);
    ui.click(ui.all(".kin-ms-bar button")[0]);
    expect(ui.last()?.value).toEqual(matching);
  });

  it("says so when a filter matches nothing", () => {
    const ui = mount({ label: "C", options: OPTIONS, value: [] });
    ui.type(ui.find(".kin-ms-filter"), "zzz");
    expect(ui.text()).toContain("no matches");
  });

  it("tolerates a spec with no options", () => {
    const ui = mount({ label: "C" });
    expect(ui.boxes()).toHaveLength(0);
    expect(ui.find(".kin-ms-count")?.textContent).toBe("0 of 0");
  });
});
