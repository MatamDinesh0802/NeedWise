import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CodeEditor } from "./code-editor";

const TEXT = "trigger:\n  event: document.deleted\nsteps:\n  - id: a\n    research: b";

describe("CodeEditor", () => {
  it("numbers the lines and marks the ones with problems", () => {
    const html = renderToStaticMarkup(
      <CodeEditor
        id="def"
        value={TEXT}
        onChange={() => {}}
        problems={[{ line: 2, where: "trigger › event", message: "Input should be 'document.ready', …" }]}
      />,
    );
    for (const line of ["1", "2", "3", "4", "5"]) expect(html).toContain(`>${line}</div>`);
    expect(html).toContain('title="Input should be &#x27;document.ready&#x27;, …"');
    expect(html).toContain("Line 2</button>");
    expect(html).toContain("trigger › event: </span>");
    expect(html).toContain('aria-invalid="true"');
    expect(html).toContain('aria-describedby="def-problems"');
    expect(html).toContain('wrap="off"');
  });

  it("lists problems without a line, and says nothing when there are none", () => {
    const html = renderToStaticMarkup(
      <CodeEditor id="def" value="" onChange={() => {}} describedBy="hint" problems={[{ line: null, where: "", message: "The definition is too long" }]} />,
    );
    expect(html).toContain("The definition is too long");
    expect(html).not.toContain("Line ");
    expect(html).toContain('aria-describedby="hint def-problems"');
    const clean = renderToStaticMarkup(<CodeEditor id="def" value="steps: []" onChange={() => {}} describedBy="hint" />);
    expect(clean).not.toContain("problem");
    expect(clean).not.toContain("aria-invalid");
    expect(clean).toContain('aria-describedby="hint"');
  });
});
