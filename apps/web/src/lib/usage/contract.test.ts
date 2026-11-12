/**
 * The usage client against responses captured from the real API (src/test/usage-api-captured.ts):
 * every shape the backend sends is one the screens read the way they were written to.
 */

import { describe, expect, it } from "vitest";

import { ApiError, type ProblemDetails } from "@/lib/api";
import { reduceRunEvent, initialRunState } from "@/lib/chat/run-reducer";
import { isEnterpriseFeatureError } from "@/lib/edition";
import {
  capturedAdminBudgets,
  capturedAdminSummary,
  capturedBudgetExceeded,
  capturedChargebackUnlicensed,
  capturedEvents,
  capturedInvalidBudgets,
  capturedManagerSummary,
  capturedMemberSummary,
  capturedRunFailed,
} from "@/test/usage-api-captured";

import { budgetBlockCopy, budgetBlockFromError, budgetBlockFromRun, isBudgetStatus } from "./budget-error";
import { formFromBudgets, problemsFromError, toUpdate, validateBudgets } from "./budgets";
import { breakdown, costChart } from "./chart";
import { budgetsForMe, groupLabel, kpis, myUsage, scopeHeading, unpricedNote } from "./format";

const now = new Date("2026-11-12T15:00:00Z");

function apiError(problem: ProblemDetails): ApiError {
  return new ApiError({
    status: problem.status,
    title: problem.title,
    code: problem.code ?? null,
    detail: problem.detail ?? null,
    problem,
  });
}

describe("the real usage API", () => {
  it("sends budget statuses the client recognises everywhere", () => {
    const statuses = [
      ...capturedAdminBudgets.budgets,
      ...capturedAdminSummary.budgets,
      ...capturedManagerSummary.budgets,
      ...capturedMemberSummary.budgets,
      capturedBudgetExceeded.budget,
      capturedRunFailed.budget,
    ];
    expect(statuses.every(isBudgetStatus)).toBe(true);
  });

  it("explains a 429 budget_exceeded with the budget, its spend and its reset", () => {
    const block = budgetBlockFromError(apiError(capturedBudgetExceeded));
    expect(block?.budget?.scope).toBe("tenant");
    const copy = budgetBlockCopy(block!, { id: "u-bob", org_role: "member" }, now);
    expect(copy.title).toBe("The monthly organization budget is used up");
    expect(copy.description).toBe("$10 of $5 spent (200%). Requests that use an AI model are paused until it resets.");
    expect(copy.resets).toMatch(/^Resets /);
  });

  it("shows a chat run stopped by a budget with that budget", () => {
    const failed = reduceRunEvent(initialRunState("r1"), { id: "1-0", type: "run.failed", data: capturedRunFailed });
    expect(failed.phase).toBe("failed");
    expect(failed.error?.code).toBe("budget_exceeded");
    expect(failed.error?.message).toBe(capturedBudgetExceeded.detail);
    expect(budgetBlockFromRun(failed.error)?.budget?.id).toBe(capturedBudgetExceeded.budget.id);
  });

  it("turns saved budgets into the form and back into the same PUT body", () => {
    const form = formFromBudgets(capturedAdminBudgets);
    expect(validateBudgets(form)).toEqual({});
    const { body } = toUpdate(form);
    expect(body).toEqual({
      budgets: [
        { scope: "tenant", subject_id: null, period: "month", limit_usd: 5, warn_at: 0.8, action: "block" },
        { scope: "user", subject_id: "u-bob", period: "day", limit_usd: 0.5, warn_at: 0.8, action: "block" },
      ],
      user_default: { period: "month", limit_usd: 20, action: "warn" },
    });
  });

  it("places the API's 422 errors on the row they are about", () => {
    const form = formFromBudgets(capturedAdminBudgets);
    const { order } = toUpdate(form);
    const placed = problemsFromError(apiError(capturedInvalidBudgets), order);
    expect(placed.fields).toEqual({ [`${order[0]}.limit`]: "Input should be greater than 0" });
    expect(placed.general).toEqual([]);
  });

  it("recognises the unlicensed chargeback report as an Enterprise feature", () => {
    expect(isEnterpriseFeatureError(apiError(capturedChargebackUnlicensed))).toBe(true);
  });

  it("draws the tenant summary and names every group", () => {
    const chart = costChart(capturedAdminSummary);
    expect(chart?.categories.length).toBe(30); // every day of November, the empty ones at 0
    const rows = breakdown(capturedAdminSummary.groups, "department").rows;
    expect(rows.map((r) => r.label)).toEqual(["People & HR", "No department", "Engineering"]);
    expect(unpricedNote(capturedAdminSummary.totals)).toBe(
      "1 of 5 requests has no price (a local model, or a model without a price), so the cost leaves it out.",
    );
    expect(kpis(capturedAdminSummary.totals).map((k) => k.value)).toEqual(["$10", "5", "4,412"]);
  });

  it("reads a manager's departments and their own share", () => {
    expect(scopeHeading(capturedManagerSummary.scope, "Acme Robotics").title).toBe("Usage in People & HR");
    expect(myUsage(capturedManagerSummary, "u-alice")).toEqual({ cost_usd: 4, requests: 1, unpriced_requests: null });
    expect(budgetsForMe(capturedManagerSummary.budgets, { id: "u-alice", departments: [] }).length).toBe(2);
  });

  it("puts features in words and keeps a member's own figures", () => {
    expect(capturedMemberSummary.groups.map((g) => groupLabel(g, "feature"))).toEqual(["Research & act", "Assistant"]);
    expect(myUsage(capturedMemberSummary, "u-bob")).toEqual({ cost_usd: 1, requests: 2, unpriced_requests: 1 });
    expect(budgetsForMe(capturedMemberSummary.budgets, { id: "u-bob", departments: [] }).map((b) => b.id)).toEqual([
      "b-tenant",
      "b-bob",
    ]);
  });

  it("lists events with names, and without a person for evaluations", () => {
    expect(capturedEvents.items.map((e) => e.user?.name ?? null)).toEqual(["Bob Okafor", null]);
    expect(capturedEvents.next_cursor).toContain("|");
  });
});
