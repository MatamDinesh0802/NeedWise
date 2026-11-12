import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { EnterpriseGateView } from "@/components/enterprise/enterprise-gate";
import type { Me } from "@/lib/session";
import { alice } from "@/test/fixtures";
import { aliceCap, selfSummary, tenantSummary } from "@/test/usage-fixtures";

import { BudgetExceededView } from "./budget-exceeded-alert";
import { BudgetMeter } from "./budget-meter";
import { BreakdownChart, ChargebackDownload, EventsRows, UsageFilters, UsageReport } from "./usage-view";

const member = { ...alice, org_role: "member", departments: [{ ...alice.departments[0], role: "member" }] } as unknown as Me;
const admin = { ...member, id: "admin-1", org_role: "admin" } as Me;
const now = new Date("2026-11-12T12:00:00Z");

describe("UsageReport for an admin (whole organization)", () => {
  const html = renderToStaticMarkup(<UsageReport summary={tenantSummary} me={admin} now={now} />);

  it("shows the KPI tiles as a description list, and explains unpriced requests", () => {
    expect(html).toMatch(/<dt[^>]*>Cost<\/dt><dd[^>]*>\$18\.75<\/dd>/);
    expect(html).toMatch(/<dt[^>]*>Requests<\/dt><dd[^>]*>640<\/dd>/);
    expect(html).toMatch(/<dt[^>]*>Tokens<\/dt><dd[^>]*>1\.56M<\/dd>/);
    expect(html).toContain("40 of 640 requests have no price");
  });

  it("draws cost over time as a labelled, keyboard-readable chart with a table view", () => {
    expect(html).toContain('aria-roledescription="chart" aria-label="Cost per day"');
    expect(html).toContain('tabindex="0"');
    expect(html).toContain('role="group" aria-label="Show as"');
  });

  it("breaks cost down by name with shares, never by id", () => {
    expect(html).toContain("Cost by department");
    for (const name of ["Engineering", "Finance", "People &amp; HR"]) expect(html).toContain(name);
    expect(html).toContain("51%");
    expect(html).not.toContain("d-eng");
    // One hue for ranked bars of nominal groups.
    expect(html.match(/background:var\(--viz-1\)/g)?.length).toBeGreaterThanOrEqual(3);
  });

  it("shows budgets as meters with the status in words and the reset time", () => {
    expect(html.match(/role="meter"/g)).toHaveLength(2);
    expect(html).toContain('aria-valuetext="$86 of $100 spent (86%). Close to the limit"');
    expect(html).toContain("Close to the limit: past the 80% warning level");
    expect(html).toContain('data-status="warning"');
    expect(html).toContain("Resets ");
    // The most urgent budget comes first.
    expect(html.indexOf("Monthly budget for Finance")).toBeLessThan(html.indexOf("Monthly organization budget"));
    expect(html).toContain('href="/admin/budgets"');
  });

  it("offers the chargeback report behind the Enterprise gate, and the request list on demand", () => {
    expect(html).toContain("Chargeback report");
    expect(html).toContain("Chargeback reports is part of WorkMind Enterprise");
    expect(html).toContain('aria-label="Checking the license"');
    expect(html).toMatch(/aria-expanded="false"[^>]*>.*Show requests/);
  });
});

describe("UsageReport for a member (own usage)", () => {
  const html = renderToStaticMarkup(<UsageReport summary={selfSummary} me={member} now={now} />);

  it("names features in words and shows their own cap", () => {
    expect(html).toContain("Cost by feature");
    expect(html).toContain("Assistant");
    expect(html).toContain("Insights");
    expect(html).toContain("Your daily cap");
    expect(html).toContain("Limit reached: 107% used");
  });

  it("has no admin tools", () => {
    expect(html).not.toContain("Chargeback report");
    expect(html).not.toContain("Show requests");
    expect(html).not.toContain('href="/admin/budgets"');
  });
});

describe("UsageReport states", () => {
  it("says when there was no usage, and still shows budgets", () => {
    const empty = {
      ...selfSummary,
      totals: { cost_usd: 0, input_tokens: 0, output_tokens: 0, requests: 0, unpriced_requests: 0 },
      series: [],
      groups: [],
    };
    const html = renderToStaticMarkup(<UsageReport summary={empty} me={member} now={now} />);
    expect(html).toContain("No model usage in this period");
    expect(html).toContain("Nov 1, 2026 – Nov 5, 2026");
    expect(html).toContain('role="meter"');
  });

  it("dims the previous answer while the next one loads", () => {
    const html = renderToStaticMarkup(<UsageReport summary={selfSummary} me={member} stale now={now} />);
    expect(html).toMatch(/class="[^"]*opacity-60[^"]*" aria-busy="true"/);
  });

  it("says there are no budgets", () => {
    const html = renderToStaticMarkup(<UsageReport summary={{ ...selfSummary, budgets: [] }} me={member} now={now} />);
    expect(html).toContain("No budget limits this usage.");
    const adminHtml = renderToStaticMarkup(<UsageReport summary={{ ...tenantSummary, budgets: [] }} me={admin} now={now} />);
    expect(adminHtml).toContain("No budgets are set, so model usage has no spending limit.");
  });
});

describe("BreakdownChart", () => {
  it("counts requests when nothing had a price, and says so", () => {
    const groups = tenantSummary.groups.map((g) => ({ ...g, cost_usd: 0, share: 0 }));
    const html = renderToStaticMarkup(<BreakdownChart groups={groups} groupBy="model" />);
    expect(html).toContain("Requests by model");
    expect(html).toContain("Nothing in this period had a price, so the bars count requests.");
  });

  it("lists the groups that don't fit as bars in the table view", () => {
    const groups = Array.from({ length: 12 }, (_, i) => ({
      key: `u${i}`,
      label: `Person ${i}`,
      cost_usd: 12 - i,
      input_tokens: 1,
      output_tokens: 1,
      requests: 1,
      share: (12 - i) / 78,
    }));
    const html = renderToStaticMarkup(<BreakdownChart groups={groups} groupBy="user" />);
    expect(html).toContain("Cost by person");
    expect(html).toContain("2 more groups are in the table view.");
    expect(html).not.toContain("Person 11");
  });
});

describe("UsageFilters", () => {
  const base = {
    onPreset: () => {},
    onCustom: () => {},
    onGroupBy: () => {},
    groupBy: "department" as const,
    groups: [
      { value: "department" as const, label: "Department" },
      { value: "user" as const, label: "Person" },
    ],
    custom: { from: "2026-11-01", to: "2026-11-12" },
    range: { from: "2026-11-01", to: "2026-11-12" },
    interval: "day" as const,
  };

  it("puts labelled filters in one group and says what range is shown", () => {
    const html = renderToStaticMarkup(<UsageFilters {...base} preset="this_month" problem={null} />);
    expect(html).toContain('role="group" aria-label="Filters"');
    expect(html).toMatch(/<label[^>]*for="([^"]+)-period"[^>]*>Period<\/label>/);
    expect(html).toMatch(/<label[^>]*for="([^"]+)-group"[^>]*>Group by<\/label>/);
    expect(html).toContain("Nov 1, 2026 – Nov 12, 2026 · per day");
    expect(html).not.toContain('type="date"');
  });

  it("shows labelled date inputs for a custom range, and the problem with it", () => {
    const html = renderToStaticMarkup(
      <UsageFilters {...base} preset="custom" custom={{ from: "2026-11-12", to: "2026-11-01" }} problem="The end date is before the start date." />,
    );
    expect(html).toMatch(/<label[^>]*for="([^"]+-from)"[^>]*>From<\/label><input[^>]*id="\1"[^>]*type="date"/);
    expect(html).toMatch(/<label[^>]*for="([^"]+-to)"[^>]*>To<\/label>/);
    expect(html).toContain('aria-invalid="true"');
    expect(html).toMatch(/aria-describedby="([^"]+-problem)".*id="\1"[^>]*>The end date is before the start date\./);
  });
});

describe("chargeback", () => {
  it("is a real download link to the department report", () => {
    const html = renderToStaticMarkup(<ChargebackDownload range={{ from: "2026-11-01", to: "2026-11-12" }} />);
    expect(html).toContain('href="/api/v1/usage/export.csv?from=2026-11-01&amp;to=2026-11-12&amp;group_by=department"');
    expect(html).toContain('download="chargeback-2026-11-01-to-2026-11-12.csv"');
    expect(html).toContain("Download chargeback CSV");
  });

  it("explains instead when the license doesn't include it", () => {
    const community = { edition: "community" as const, features: [], licensee: null, expires_at: null };
    const html = renderToStaticMarkup(
      <EnterpriseGateView feature="chargeback_reports" edition={community}>
        <ChargebackDownload range={{ from: "2026-11-01", to: "2026-11-12" }} />
      </EnterpriseGateView>,
    );
    expect(html).toContain("Enterprise feature: Chargeback reports");
    expect(html).not.toContain("Download chargeback CSV");
  });
});

describe("EventsRows", () => {
  it("shows names, features in words, local models and unpriced requests", () => {
    const html = renderToStaticMarkup(
      <EventsRows
        items={[
          {
            id: "e1",
            occurred_at: "2026-11-12T09:30:00Z",
            user: { id: "u1", name: "Alice Anders" },
            department: { id: "d1", name: "People & HR" },
            agent_key: "hr",
            feature: "agents",
            model: "llama3.1:8b",
            provider: "ollama",
            local: true,
            input_tokens: 1200,
            output_tokens: 300,
            cost_usd: null,
            ref: null,
          },
          {
            id: "e2",
            occurred_at: "2026-11-12T09:31:00Z",
            user: null,
            department: null,
            agent_key: null,
            feature: "workflow",
            model: "claude-sonnet-5-5",
            provider: "anthropic",
            local: false,
            input_tokens: 5000,
            output_tokens: 800,
            cost_usd: 0.027,
            ref: { type: "workflow_run", id: "r1" },
          },
        ]}
      />,
    );
    expect(html).toContain("Alice Anders");
    expect(html).toContain("Department agents");
    expect(html).toContain(">local<");
    expect(html).toContain("No price");
    expect(html).toContain("1,200 / 300");
    expect(html).toContain("No person (system)");
    expect(html).toContain("Workflows");
    expect(html).toContain("$0.03");
  });
});

describe("BudgetMeter", () => {
  it("is a labelled meter whose value never passes the limit", () => {
    const html = renderToStaticMarkup(<BudgetMeter budget={aliceCap} meId={alice.id} now={now} />);
    expect(html).toMatch(/<p id="([^"]+)"[^>]*>Your daily cap<\/p>.*role="meter" aria-labelledby="\1"/);
    expect(html).toContain('aria-valuemax="2"');
    expect(html).toContain('aria-valuenow="2"');
    expect(html).toContain("width:100%");
    expect(html).toContain("var(--viz-critical)");
  });
});

describe("BudgetExceededView", () => {
  it("names the budget, when it resets, and who to ask", () => {
    const html = renderToStaticMarkup(<BudgetExceededView block={{ detail: null, budget: aliceCap }} viewer={{ id: alice.id, org_role: "member" }} now={now} />);
    expect(html).toContain('role="alert"');
    expect(html).toContain("Your daily cap is used up");
    expect(html).toContain("$2.13 of $2 spent (107%)");
    expect(html).toContain("Resets ");
    expect(html).toContain("Ask an owner or admin of your organization to raise your cap.");
    expect(html).toContain('href="/analytics"');
    expect(html).not.toContain('href="/admin/budgets"');
  });

  it("gives admins a way to change it", () => {
    const html = renderToStaticMarkup(<BudgetExceededView block={{ detail: null, budget: aliceCap }} viewer={{ id: "a", org_role: "owner" }} now={now} />);
    expect(html).toContain('href="/admin/budgets"');
  });
});
