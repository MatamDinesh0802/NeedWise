import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { VerifiedNumber } from "@/lib/analytics/api";

import { Narrative, queryAnchor } from "./narrative";

const numbers: VerifiedNumber[] = [
  { text: "€5.02M", value: 5020000, verified: true, query: "Q1" },
  { text: "12%", value: 12, verified: false, query: null },
];

const render = (text: string, queryIds = ["Q1", "Q2"]) =>
  renderToStaticMarkup(<Narrative text={text} numbers={numbers} answerId="a1" queryIds={queryIds} />);

describe("Narrative", () => {
  it("marks verified figures quietly and unverified ones visibly, for screen readers too", () => {
    const html = render("Revenue reached **€5.02M**, up 12% on Q2.");
    expect(html).toContain('<data value="5020000" data-verified="true" title="Verified: matches Q1&#x27;s results"');
    expect(html).toContain("<strong><data");
    expect(html).toContain("(verified in Q1)");
    expect(html).toContain('data-verified="false"');
    expect(html).toContain("(not verified: check it against the tables)");
    expect(html).toContain("Unverified. Not found in any query result.");
  });

  it("links [Q1] markers to the query and leaves unknown ones as text", () => {
    const html = render("See the trend [Q1] and the drivers [Q1, Q3].", ["Q1"]);
    expect(html).toContain(`href="#${queryAnchor("a1", "Q1")}"`);
    expect(html).toContain('aria-label="Query 1: see the SQL and results"');
    expect(html).toContain("[Q3]");
    expect(html.match(/<a /g)).toHaveLength(2);
  });

  it("never marks figures inside code, and never renders raw HTML", () => {
    const html = render("Use `SELECT 12%` literally. <script>alert(1)</script> 12%");
    expect(html).toContain("<code");
    expect(html).toContain("SELECT 12%");
    expect(html.match(/data-verified="false"/g)).toHaveLength(1);
    expect(html).not.toContain("<script");
  });
});
