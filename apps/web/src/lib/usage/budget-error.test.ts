import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { aliceCap, financeBudget, tenantBudget } from "@/test/usage-fixtures";

import { budgetBlockCopy, budgetBlockFromError, budgetBlockFromRun, isBudgetStatus } from "./budget-error";

const now = new Date("2026-11-12T12:00:00Z");
const member = { id: "0190f2c4-0000-7000-8000-000000000001", org_role: "member" as const };
const admin = { id: "admin-1", org_role: "admin" as const };

function exceeded(budget: unknown) {
  return new ApiError({
    status: 429,
    title: "Budget exceeded",
    code: "budget_exceeded",
    detail: "The organization's monthly budget is used up.",
    problem: { type: "about:blank", title: "Budget exceeded", status: 429, code: "budget_exceeded", detail: "The organization's monthly budget is used up.", budget },
  });
}

describe("budgetBlockFromError", () => {
  it("reads the budget from a 429 budget_exceeded problem", () => {
    expect(budgetBlockFromError(exceeded(tenantBudget))).toEqual({ detail: "The organization's monthly budget is used up.", budget: tenantBudget });
  });

  it("keeps the detail when the budget is missing or malformed", () => {
    expect(budgetBlockFromError(exceeded({ id: 1 }))).toEqual({ detail: "The organization's monthly budget is used up.", budget: null });
  });

  it("ignores every other error", () => {
    expect(budgetBlockFromError(new ApiError({ status: 429, title: "Too many requests", code: "rate_limited" }))).toBeNull();
    expect(budgetBlockFromError(new Error("budget_exceeded"))).toBeNull();
    expect(budgetBlockFromError(null)).toBeNull();
  });
});

describe("budgetBlockFromRun", () => {
  it("reads the stream's run.failed error, with or without the budget", () => {
    expect(budgetBlockFromRun({ code: "budget_exceeded", message: "Your daily cap is used up.", budget: aliceCap })).toEqual({
      detail: "Your daily cap is used up.",
      budget: aliceCap,
    });
    expect(budgetBlockFromRun({ code: "budget_exceeded", message: "" })).toEqual({ detail: null, budget: null });
    expect(budgetBlockFromRun({ code: "internal", message: "boom" })).toBeNull();
    expect(budgetBlockFromRun(null)).toBeNull();
  });
});

describe("isBudgetStatus", () => {
  it("accepts the contract's shape and rejects others", () => {
    expect(isBudgetStatus(financeBudget)).toBe(true);
    expect(isBudgetStatus({ ...financeBudget, scope: "team" })).toBe(false);
    expect(isBudgetStatus({ ...financeBudget, subject: { id: 3 } })).toBe(false);
    expect(isBudgetStatus("budget")).toBe(false);
  });
});

describe("budgetBlockCopy", () => {
  it("names the budget, the spend, the reset and who to ask", () => {
    const copy = budgetBlockCopy({ detail: null, budget: tenantBudget }, member, now);
    expect(copy.title).toBe("The monthly organization budget is used up");
    expect(copy.description).toBe("$212.40 of $500 spent (42%). Requests that use an AI model are paused until it resets.");
    expect(copy.resets).toMatch(/^Resets .+ \(in 3 weeks\)$/);
    expect(copy.who).toBe("Ask an owner or admin of your organization if you need more.");
    expect(copy.manageHref).toBeNull();
  });

  it("speaks to the person about their own cap, and to the team about a department", () => {
    expect(budgetBlockCopy({ detail: null, budget: aliceCap }, member, now)).toMatchObject({
      title: "Your daily cap is used up",
      who: "Ask an owner or admin of your organization to raise your cap.",
    });
    expect(budgetBlockCopy({ detail: null, budget: financeBudget }, member, now)).toMatchObject({
      title: "The monthly budget for Finance is used up",
      who: "Ask your department manager or an owner or admin of your organization if your team needs more.",
    });
  });

  it("sends admins to Admin › Budgets", () => {
    const copy = budgetBlockCopy({ detail: null, budget: financeBudget }, admin, now);
    expect(copy.who).toContain("Admin › Budgets");
    expect(copy.manageHref).toBe("/admin/budgets");
  });

  it("falls back to the API's words without a budget", () => {
    const copy = budgetBlockCopy({ detail: "Usage budget reached.", budget: null }, null, now);
    expect(copy).toMatchObject({ title: "A usage budget is used up", description: "Usage budget reached.", resets: null });
    expect(budgetBlockCopy({ detail: null, budget: null }, null, now).description).toContain("paused until the budget resets");
  });
});
