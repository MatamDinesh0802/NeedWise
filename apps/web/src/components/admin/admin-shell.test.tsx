import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AdminShell, PageSlot } from "./admin-shell";

describe("AdminShell on the server (the session is not known yet)", () => {
  it("renders the page, hidden, so Next.js can validate its instant navigation", () => {
    const html = renderToStaticMarkup(
      <AdminShell>
        <p>People page</p>
      </AdminShell>,
    );
    // The page is in the markup (not dropped), inside a hidden element, behind a skeleton.
    expect(html).toMatch(/<div hidden="">\s*<p>People page<\/p>\s*<\/div>/);
    expect(html).toContain('aria-label="Loading administration"');
    // Nothing that needs the session yet: no navigation, no tenant name.
    expect(html).not.toContain("Administration sections");
  });
});

describe("PageSlot", () => {
  it("shows the page in place once access is known", () => {
    const html = renderToStaticMarkup(
      <PageSlot visible>
        <p>People page</p>
      </PageSlot>,
    );
    expect(html).toBe('<div class="contents"><p>People page</p></div>');
  });
});
