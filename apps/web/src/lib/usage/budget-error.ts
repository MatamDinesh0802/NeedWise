/**
 * "budget_exceeded": a budget with action "block" is used up, so model-using requests (chat,
 * agents, research, Insights, the agent test console) are refused with 429 and the budget's
 * status; streaming chat sends the same code in its run.failed event. This turns either into
 * a message that names the budget, says when it resets and who can raise it. Pure and tested.
 */

import { ApiError } from "@/lib/api";
import type { OrgRole } from "@/lib/session";
import type { BudgetStatus } from "@/lib/usage/api";
import { resetText, spentText } from "@/lib/usage/format";

export const BUDGET_EXCEEDED = "budget_exceeded";
export const BUDGETS_ADMIN_HREF = "/admin/budgets";

export interface BudgetBlock {
  /** The API's own words, when it sent them. */
  detail: string | null;
  /** The budget that is used up; null when the response didn't include it. */
  budget: BudgetStatus | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A loose shape check: enough to name the budget and its reset without trusting the rest. */
export function isBudgetStatus(value: unknown): value is BudgetStatus {
  if (!isRecord(value)) return false;
  const subjectOk =
    value.subject === null || (isRecord(value.subject) && typeof value.subject.id === "string" && typeof value.subject.name === "string");
  return (
    typeof value.id === "string" &&
    (value.scope === "tenant" || value.scope === "department" || value.scope === "user") &&
    subjectOk &&
    (value.period === "day" || value.period === "month") &&
    typeof value.limit_usd === "number" &&
    typeof value.spent_usd === "number" &&
    typeof value.resets_at === "string"
  );
}

/** The budget block behind an API error, or null for any other error. */
export function budgetBlockFromError(error: unknown): BudgetBlock | null {
  if (!(error instanceof ApiError) || error.code !== BUDGET_EXCEEDED) return null;
  const budget = error.problem?.budget;
  return { detail: error.detail, budget: isBudgetStatus(budget) ? budget : null };
}

/** The budget block behind a failed run (the stream's run.failed event), or null. */
export function budgetBlockFromRun(error: { code: string; message: string; budget?: unknown } | null | undefined): BudgetBlock | null {
  if (!error || error.code !== BUDGET_EXCEEDED) return null;
  return { detail: error.message || null, budget: isBudgetStatus(error.budget) ? error.budget : null };
}

export interface Viewer {
  id: string;
  org_role: OrgRole;
}

export interface BudgetBlockCopy {
  title: string;
  description: string;
  /** "Resets Dec 1, 2026, 1:00 AM (in 19 days)"; null when the budget isn't known. */
  resets: string | null;
  /** Who can raise the limit. */
  who: string;
  /** Where an owner or admin changes budgets; null for everyone else. */
  manageHref: string | null;
}

/** A person's cap only ever blocks their own requests, so a user budget here is always "your cap". */
function budgetPhrase(budget: BudgetStatus): string {
  const period = budget.period === "day" ? "daily" : "monthly";
  if (budget.scope === "tenant") return `The ${period} organization budget`;
  if (budget.scope === "department") return `The ${period} budget for ${budget.subject?.name ?? "your department"}`;
  return `Your ${period} cap`;
}

export function budgetBlockCopy(block: BudgetBlock, viewer: Viewer | null, now: Date = new Date()): BudgetBlockCopy {
  const admin = viewer?.org_role === "owner" || viewer?.org_role === "admin";
  const budget = block.budget;
  let who: string;
  if (admin) who = "You can raise the limit or change the budget in Admin › Budgets.";
  else if (budget?.scope === "department") who = "Ask your department manager or an owner or admin of your organization if your team needs more.";
  else if (budget?.scope === "user") who = "Ask an owner or admin of your organization to raise your cap.";
  else who = "Ask an owner or admin of your organization if you need more.";

  if (!budget) {
    return {
      title: "A usage budget is used up",
      description: block.detail ?? "Requests that use an AI model are paused until the budget resets.",
      resets: null,
      who,
      manageHref: admin ? BUDGETS_ADMIN_HREF : null,
    };
  }
  return {
    title: `${budgetPhrase(budget)} is used up`,
    description: `${spentText(budget)}. Requests that use an AI model are paused until it resets.`,
    resets: resetText(budget.resets_at, now),
    who,
    manageHref: admin ? BUDGETS_ADMIN_HREF : null,
  };
}
