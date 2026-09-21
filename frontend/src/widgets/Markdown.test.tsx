import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Markdown } from "./Markdown";
import type { UiNode } from "../types";

const md = (value: string) =>
  renderToStaticMarkup(
    <Markdown
      node={{ id: "markdown_1", kind: "markdown", props: { value } } as UiNode}
      send={() => {}}
    />,
  );

const hrefs = (html: string) =>
  (html.match(/href="[^"]*"/g) ?? []).map((h) => h.slice(6, -1));

describe("links", () => {
  // Regression: `inline` recurses into link text, and the scanner used to be a
  // module-level /g RegExp — the inner call reset `lastIndex`, so the outer scan
  // restarted at 0 forever and froze the tab on any markdown containing a link.
  it("renders a link once and keeps scanning past it", () => {
    const html = md("before [the docs](https://example.com) after");
    expect(html).toContain(">the docs</a>");
    expect(html.match(/the docs/g)).toHaveLength(1);
    expect(html.match(/after/g)).toHaveLength(1);
    expect(html).toContain("before ");
  });

  it("handles several links in one paragraph", () => {
    const html = md(
      "[a](https://a.test) [b](https://b.test) [c](https://c.test)",
    );
    expect(hrefs(html)).toEqual([
      "https://a.test",
      "https://b.test",
      "https://c.test",
    ]);
    expect(html.match(/<a /g)).toHaveLength(3);
  });

  it("formats inside link text", () => {
    expect(md("[**bold link**](https://x.test)")).toContain(
      "<strong>bold link</strong>",
    );
  });

  it("allows relative targets and blocks script-bearing schemes", () => {
    expect(hrefs(md("[a](docs/page.html) [b](#frag) [c](/root)"))).toEqual([
      "docs/page.html",
      "#frag",
      "/root",
    ]);
    expect(hrefs(md("[x](javascript:alert(1))"))).toEqual(["#"]);
    expect(hrefs(md("[x](data:text/html;base64,PHNjcmlwdD4=)"))).toEqual(["#"]);
    expect(hrefs(md("[x](https://ok.test/a?b=1)"))).toEqual([
      "https://ok.test/a?b=1",
    ]);
  });
});

describe("blocks", () => {
  it("renders headings at their level", () => {
    expect(md("# One")).toContain("<h1");
    expect(md("### Three")).toContain("<h3");
    expect(md("###### Six")).toContain("<h6");
  });

  it("renders both list flavours", () => {
    const ul = md("- one\n- two");
    expect(ul).toContain("<ul");
    expect(ul.match(/<li>/g)).toHaveLength(2);
    const ol = md("1. one\n2. two");
    expect(ol).toContain("<ol");
    expect(ol.match(/<li>/g)).toHaveLength(2);
  });

  it("treats a rule as a rule, not a bullet", () => {
    expect(md("---")).toContain("<hr");
    expect(md("---")).not.toContain("<li>");
  });

  it("renders blockquotes and nests blocks inside them", () => {
    const html = md("> quoted **text**\n> still quoted");
    expect(html).toContain("<blockquote");
    expect(html).toContain("<strong>text</strong>");
  });

  it("keeps fenced code verbatim", () => {
    const html = md("```python\napp.markdown('**not bold**')\n```");
    expect(html).toContain("<pre");
    expect(html).not.toContain("<strong>");
    expect(html).toContain("**not bold**");
  });

  it("renders the inline run", () => {
    const html = md("plain **b** *i* _i2_ ~~s~~ `c`");
    expect(html).toContain("<strong>b</strong>");
    expect(html).toContain("<em>i</em>");
    expect(html).toContain("<em>i2</em>");
    expect(html).toContain("<del>s</del>");
    expect(html).toContain(">c</code>");
  });
});

describe("malformed input still terminates", () => {
  // Each of these once risked a non-advancing scan; the parser carries a progress
  // budget so a regression fails the assertion instead of hanging the worker.
  it("survives a fence whose info string has spaces and is never closed", () => {
    const html = md('```python title="x"\nbody line\n');
    expect(html).toContain("body line");
  });

  it("survives an unclosed fence", () => {
    expect(md("```\nno close")).toContain("no close");
  });

  it("survives stray markers", () => {
    expect(md("**unclosed and `also unclosed")).toContain("unclosed");
    expect(md("[link with no target")).toContain("link with no target");
  });

  it("renders empty and whitespace-only input without error", () => {
    expect(md("")).toBe('<div class="kin-md"></div>');
    expect(md("\n\n   \n")).toBe('<div class="kin-md"></div>');
  });
});

describe("no HTML injection", () => {
  it("escapes raw HTML rather than rendering it", () => {
    const html = md("<script>alert(1)</script> and <b>tags</b>");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<b>tags</b>");
    expect(html).toContain("&lt;script&gt;");
  });
});
