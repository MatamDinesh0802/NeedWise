import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ScopeBadge } from "./badges";

describe("ScopeBadge", () => {
  it("draws Restricted with the warning tokens (4.5:1 or more in both themes), not a fixed amber", () => {
    const html = renderToStaticMarkup(<ScopeBadge scope="restricted" />);
    expect(html).toContain("bg-warning text-warning-foreground");
    expect(html).not.toMatch(/amber-\d{3}/);
    expect(html).toContain(">Restricted<");
  });

  it("keeps the other scopes on the secondary badge", () => {
    const html = renderToStaticMarkup(<ScopeBadge scope="department" />);
    expect(html).toContain('data-variant="secondary"');
    expect(html).not.toContain("bg-warning");
  });
});
