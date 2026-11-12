import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { alice } from "@/test/fixtures";
import { aliceCap, financeBudget, selfSummary, tenantBudget, tenantSummary } from "@/test/usage-fixtures";

import { UsageTileBody } from "./usage-tile";

const now = new Date("2026-11-12T12:00:00Z");
const me = { id: alice.id, departments: [...alice.departments] };

describe("UsageTileBody", () => {
  it("shows my own cost and requests, my tightest budget and a link to the Usage page", () => {
    const html = renderToStaticMarkup(<UsageTileBody summary={selfSummary} me={me} now={now} />);
    expect(html).toContain("$1.20");
    expect(html).toContain("31 requests");
    expect(html).toContain("Your daily cap");
    expect(html).toContain('role="meter"');
    expect(html).toContain('href="/analytics"');
  });

  it("finds me among the people when the summary covers more than me", () => {
    const summary = {
      ...tenantSummary,
      group_by: "user" as const,
      groups: [
        { key: "someone", label: "Someone", cost_usd: 9, input_tokens: 1, output_tokens: 1, requests: 90, share: 0.9 },
        { key: alice.id, label: "Alice Anders", cost_usd: 1, input_tokens: 1, output_tokens: 1, requests: 1, share: 0.1 },
      ],
      budgets: [tenantBudget, financeBudget, { ...aliceCap, id: "b-other", subject: { id: "someone", name: "Someone" } }],
    };
    const html = renderToStaticMarkup(<UsageTileBody summary={summary} me={me} now={now} />);
    expect(html).toContain("$1");
    expect(html).toContain("1 request");
    // Finance is not my department and Someone's cap is not mine: only the organization budget applies.
    expect(html).toContain("Monthly organization budget");
    expect(html).not.toContain("Finance");
    expect(html).not.toContain("Someone");
  });

  it("shows zero when I made no requests, and says when no budget applies", () => {
    const summary = { ...tenantSummary, group_by: "user" as const, groups: [], budgets: [] };
    const html = renderToStaticMarkup(<UsageTileBody summary={summary} me={me} now={now} />);
    expect(html).toContain("$0");
    expect(html).toContain("0 requests");
    expect(html).toContain("No budget limits your usage.");
  });
});
