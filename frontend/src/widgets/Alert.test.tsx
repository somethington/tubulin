import { describe, expect, it } from "vitest";
import { Alert } from "./Alert";
import { node, render } from "../test-utils";
import type { UiNode } from "../types";

const mount = (props: Record<string, unknown>) =>
  render(<Alert node={node("alert", props) as UiNode} send={() => {}} />);

describe("Alert", () => {
  it("colours itself by level", () => {
    for (const level of ["info", "success", "warning", "error"]) {
      const ui = mount({ level, value: "hi" });
      expect(ui.find(`.kin-alert-${level}`)).not.toBeNull();
    }
  });

  // A level the frontend doesn't know (an older bundle against a newer runtime)
  // must still render its text rather than vanish.
  it("falls back to info for an unknown level", () => {
    const ui = mount({ level: "critical", value: "still visible" });
    expect(ui.find(".kin-alert-info")).not.toBeNull();
    expect(ui.text()).toContain("still visible");
  });

  it("renders the body through the markdown subset", () => {
    const ui = mount({
      level: "warning",
      value: "Only **two** replicates — see [method](https://example.com).",
    });
    expect(ui.find("strong")?.textContent).toBe("two");
    expect(ui.find("a")?.getAttribute("href")).toBe("https://example.com");
  });

  it("shows a title only when one is given", () => {
    expect(mount({ level: "info", value: "b" }).find(".kin-alert-title")).toBeNull();
    expect(
      mount({ level: "info", value: "b", title: "Heads up" }).find(
        ".kin-alert-title",
      )?.textContent,
    ).toBe("Heads up");
  });

  // Errors interrupt; guidance waits its turn.
  it("announces errors assertively and everything else politely", () => {
    expect(mount({ level: "error", value: "x" }).find('[role="alert"]')).not.toBeNull();
    expect(
      mount({ level: "info", value: "x" }).find('[aria-live="polite"]'),
    ).not.toBeNull();
  });
});
