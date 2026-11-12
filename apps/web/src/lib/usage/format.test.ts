import { describe, expect, it } from "vitest";

import type { Me } from "@/lib/session";
import { alice } from "@/test/fixtures";
import { aliceCap, financeBudget, selfSummary, tenantBudget, tenantSummary } from "@/test/usage-fixtures";

import {
  budgetsForMe,
  budgetTitle,
  defaultGroupBy,
  expectedScope,
  featureLabel,
  formatShare,
  formatUsd,
  groupLabel,
  groupOptions,
  joinNames,
  kpis,
  myUsage,
  resetText,
  scopeHeading,
  sortBudgets,
  spentText,
  statusDetail,
  unpricedNote,
  usedShare,
} from "./format";

const member = { ...alice, org_role: "member", departments: [{ ...alice.departments[0], role: "member" }] } as unknown as Me;
const manager = alice as unknown as Me;
const admin = { ...member, org_role: "admin" } as Me;

describe("scope", () => {
  it("follows the contract: admins see the tenant, managers their departments, others themselves", () => {
    expect(expectedScope(admin)).toBe("tenant");
    expect(expectedScope({ ...admin, org_role: "owner" })).toBe("tenant");
    expect(expectedScope(manager)).toBe("departments");
    expect(expectedScope(member)).toBe("self");
    expect(expectedScope({ ...member, org_role: "auditor" })).toBe("self");
  });

  it("says whose usage the page covers", () => {
    expect(scopeHeading({ kind: "tenant", departments: [] }, "Acme Robotics")).toEqual({
      title: "Usage across the whole organization",
      description: "Every model request made by anyone in Acme Robotics.",
    });
    const one = scopeHeading({ kind: "departments", departments: [{ id: "d1", name: "People & HR" }] }, "Acme");
    expect(one.title).toBe("Usage in People & HR");
    expect(one.description).toBe("Requests by the people in People & HR, the department you manage.");
    const two = scopeHeading(
      { kind: "departments", departments: [{ id: "d1", name: "People & HR" }, { id: "d2", name: "Finance" }, { id: "d3", name: "Legal" }] },
      "Acme",
    );
    expect(two.title).toBe("Usage in your departments");
    expect(two.description).toContain("People & HR, Finance and Legal, the departments you manage");
    expect(scopeHeading({ kind: "self", departments: [] }, "Acme").title).toBe("Your own usage");
  });

  it("joins names in plain English", () => {
    expect(joinNames([])).toBe("");
    expect(joinNames(["A"])).toBe("A");
    expect(joinNames(["A", "B"])).toBe("A and B");
  });
});

describe("grouping", () => {
  it("doesn't offer person or department for your own usage", () => {
    expect(groupOptions("self").map((o) => o.value)).toEqual(["feature", "model", "agent"]);
    expect(groupOptions("tenant").map((o) => o.label)).toEqual(["Department", "Person", "Agent", "Model", "Feature"]);
    expect(defaultGroupBy("tenant")).toBe("department");
    expect(defaultGroupBy("departments")).toBe("user");
    expect(defaultGroupBy("self")).toBe("feature");
  });

  it("names features in words and keeps the API's labels otherwise", () => {
    expect(featureLabel("analytics")).toBe("Insights");
    expect(featureLabel("agent_test")).toBe("Agent test console");
    expect(featureLabel("something_new")).toBe("something_new");
    expect(groupLabel({ key: "chat", label: "chat" }, "feature")).toBe("Assistant");
    expect(groupLabel({ key: "chat", label: "Chat (assistant)" }, "feature")).toBe("Chat (assistant)");
    expect(groupLabel({ key: "0190-u1", label: "Alice Anders" }, "user")).toBe("Alice Anders");
    expect(groupLabel({ key: "x", label: "" }, "agent")).toBe("Unnamed");
  });

  it("writes shares as whole percents, and tiny ones as <1%", () => {
    expect(formatShare(0.5067)).toBe("51%");
    expect(formatShare(0.004)).toBe("<1%");
    expect(formatShare(0)).toBe("0%");
    expect(formatShare(1)).toBe("100%");
  });
});

describe("KPIs", () => {
  it("shows cost, requests and tokens, compact from 10,000", () => {
    const [cost, requests, tokens] = kpis(tenantSummary.totals);
    expect(cost).toEqual({ key: "cost", label: "Cost", value: "$18.75", detail: "Excludes 40 unpriced requests" });
    expect(requests.value).toBe("640");
    expect(requests.detail).toBe("$0.03 per priced request on average");
    expect(tokens.value).toBe("1.56M");
    expect(tokens.detail).toBe("1.25M in · 310K out");
  });

  it("has nothing to add when there was no usage", () => {
    const tiles = kpis({ cost_usd: 0, input_tokens: 0, output_tokens: 0, requests: 0, unpriced_requests: 0 });
    expect(tiles.map((t) => t.value)).toEqual(["$0", "0", "0"]);
    expect(tiles.every((t) => t.detail === null)).toBe(true);
  });

  it("explains unpriced requests", () => {
    expect(unpricedNote({ requests: 640, unpriced_requests: 0 })).toBeNull();
    expect(unpricedNote({ requests: 640, unpriced_requests: 40 })).toBe(
      "40 of 640 requests have no price (a local model, or a model without a price), so the cost leaves them out.",
    );
    expect(unpricedNote({ requests: 640, unpriced_requests: 1 })).toContain("1 of 640 requests has no price");
    expect(unpricedNote({ requests: 12, unpriced_requests: 12 })).toContain("All 12 requests have no price");
    expect(unpricedNote({ requests: 1, unpriced_requests: 1 })).toContain("The only request has no price");
  });
});

describe("myUsage", () => {
  it("uses the totals when the scope is only me, and my group otherwise", () => {
    expect(myUsage(selfSummary, alice.id)).toEqual({ cost_usd: 1.2, requests: 31, unpriced_requests: 0 });
    const byUser = { ...tenantSummary, group_by: "user" as const, groups: [{ ...tenantSummary.groups[0], key: alice.id }] };
    expect(myUsage(byUser, alice.id)).toEqual({ cost_usd: 9.5, requests: 300, unpriced_requests: null });
    expect(myUsage(byUser, "nobody")).toEqual({ cost_usd: 0, requests: 0, unpriced_requests: null });
    // Grouped by something else, a department id must never be read as mine.
    expect(myUsage({ ...tenantSummary, groups: [{ ...tenantSummary.groups[0], key: alice.id }] }, alice.id).requests).toBe(0);
  });
});

describe("formatUsd", () => {
  it("keeps cents, and two significant digits under a cent", () => {
    expect(formatUsd(18.75)).toBe("$18.75");
    expect(formatUsd(500)).toBe("$500");
    expect(formatUsd(0.0042)).toBe("$0.0042");
    expect(formatUsd(0.00001)).toBe("<$0.0001");
    expect(formatUsd(0)).toBe("$0");
    expect(formatUsd(12_345, "compact")).toBe("$12.3K");
    expect(formatUsd(12_345)).toBe("$12,345");
    expect(formatUsd(Number.NaN)).toBe("—");
  });
});

describe("budgets", () => {
  it("names each budget by scope, period and subject", () => {
    expect(budgetTitle(tenantBudget, null)).toBe("Monthly organization budget");
    expect(budgetTitle(financeBudget, null)).toBe("Monthly budget for Finance");
    expect(budgetTitle(aliceCap, alice.id)).toBe("Your daily cap");
    expect(budgetTitle(aliceCap, "someone-else")).toBe("Daily cap for Alice Anders");
    expect(budgetTitle({ ...aliceCap, subject: null }, "someone-else")).toBe("Your daily cap");
  });

  it("states status in words, with the numbers", () => {
    expect(spentText(tenantBudget)).toBe("$212.40 of $500 spent (42%)");
    expect(statusDetail(tenantBudget)).toBe("Within budget: warns at 80%");
    expect(statusDetail(financeBudget)).toBe("Close to the limit: past the 80% warning level");
    expect(statusDetail(aliceCap)).toBe("Limit reached: 107% used");
    expect(usedShare({ spent_usd: 1, limit_usd: 0 })).toBe(0);
  });

  it("says when a budget resets, relative to now", () => {
    const text = resetText("2026-12-01T00:00:00Z", new Date("2026-11-12T00:00:00Z"));
    expect(text).toMatch(/^Resets .+2026.+ \(in 3 weeks\)$/);
    expect(resetText("2026-11-13T00:00:00Z", new Date("2026-11-12T15:00:00Z"))).toMatch(/\(in 9 hours\)$/);
    expect(resetText("not a date")).toBe("Reset time unknown");
  });

  it("puts the most urgent budget first", () => {
    expect(sortBudgets([tenantBudget, aliceCap, financeBudget]).map((b) => b.id)).toEqual(["b-alice", "b-finance", "b-tenant"]);
  });

  it("keeps only the budgets that limit my own requests", () => {
    const other = { ...aliceCap, id: "b-bob", subject: { id: "bob", name: "Bob" } };
    const hr = { ...financeBudget, id: "b-hr", subject: { id: "d1", name: "People & HR" } };
    const mine = budgetsForMe([tenantBudget, financeBudget, hr, aliceCap, other], manager);
    expect(mine.map((b) => b.id)).toEqual(["b-tenant", "b-hr", "b-alice"]);
  });
});
