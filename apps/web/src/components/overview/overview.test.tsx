import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Overview } from "./overview";

describe("Overview", () => {
  it("is a page of its own, not a placeholder for a past phase", () => {
    const html = renderToStaticMarkup(<Overview />);
    expect(html).toContain("Overview");
    expect(html).not.toContain("Arrives in Phase");
  });

  it("no longer says usage summaries arrive later", () => {
    const html = renderToStaticMarkup(<Overview />);
    expect(html).not.toContain("Phase 11");
    expect(html).toContain("Your model usage this month");
  });
});
