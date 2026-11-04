import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AnswerMarkdown, type ChipInfo } from "./answer-markdown";

const sources = new Map<string, ChipInfo>([
  ["S1", { title: "Leave Policy 2026", cited: true }],
  ["S2", { title: "HR FAQ", cited: false }],
  ["S3", { title: "Contractor Handbook", cited: true }],
]);

const render = (text: string) => renderToStaticMarkup(<AnswerMarkdown text={text} sources={sources} />);
const chips = (html: string) => [...html.matchAll(/aria-label="Source (\d+)[^"]*"/g)].map((m) => m[1]);

describe("AnswerMarkdown", () => {
  it("renders [S1] and [S1, S3] as keyboard-focusable chip buttons", () => {
    const html = render("You get **16 weeks** [S1]. Contractors differ [S1, S3].");
    expect(chips(html)).toEqual(["1", "1", "3"]);
    expect(html).toContain('<button type="button"');
    expect(html).toContain('aria-label="Source 1: Leave Policy 2026"');
    expect(html).toContain("<strong>16 weeks</strong>");
    expect(html).not.toContain("[S1]");
  });

  it("leaves markers inside inline code and code blocks alone", () => {
    const html = render("Use `[S1]` literally.\n\n```\nprint('[S2, S3]')\n```\n\nBut cite this [S2].");
    expect(html).toContain("<code");
    expect(html).toContain("[S1]");
    expect(html).toContain("[S2, S3]");
    expect(chips(html)).toEqual(["2"]); // only the marker in plain text
  });

  it("marks unknown markers as unavailable instead of linking them", () => {
    const html = render("Odd claim [S9].");
    expect(html).toContain("Source 9 (not among the retrieved sources)");
    expect(html).toMatch(/<button[^>]*disabled/);
  });

  it("renders GFM tables", () => {
    const html = render("| City | Limit |\n| --- | --- |\n| London | £200 [S2] |");
    expect(html).toContain("<table");
    expect(html).toContain("<td");
    expect(chips(html)).toEqual(["2"]);
  });

  it("never renders raw HTML or javascript: links", () => {
    const html = render('<script>alert(1)</script><img src=x onerror="alert(2)"> [click](javascript:alert(3))');
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("javascript:");
  });

  it("opens links in a new tab without opener access", () => {
    const html = render("[policy](https://intranet.example/leave)");
    expect(html).toContain('href="https://intranet.example/leave"');
    expect(html).toContain('rel="noopener noreferrer nofollow"');
  });
});
