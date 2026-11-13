/**
 * Words and numbers for the usage page: who the figures cover, group and feature names, KPI
 * tiles, and budget status. Framework-free and unit-tested.
 */

import { formatNumber, formatUsd } from "@/lib/analytics/format";
import { formatDateTime, formatRelative } from "@/lib/format";
import type { Me } from "@/lib/session";
import type {
  BudgetPeriod,
  BudgetState,
  BudgetStatus,
  GroupBy,
  ScopeKind,
  UsageFeature,
  UsageGroup,
  UsageScope,
  UsageSummary,
  UsageTotals,
} from "@/lib/usage/api";

export { formatUsd };

/** Org roles that manage budgets and see the whole tenant's usage. Cosmetic: the API decides. */
export const USAGE_ADMIN_ROLES = ["owner", "admin"] as const;

export function isUsageAdmin(me: Pick<Me, "org_role">): boolean {
  return (USAGE_ADMIN_ROLES as readonly string[]).includes(me.org_role);
}

/** The scope the API will answer with for this person (the response's `scope` is the truth). */
export function expectedScope(me: Pick<Me, "org_role" | "departments">): ScopeKind {
  if (isUsageAdmin(me)) return "tenant";
  return me.departments.some((d) => d.role === "manager") ? "departments" : "self";
}

/** "A", "A and B", "A, B and C". */
export function joinNames(names: readonly string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** The page heading and one line saying whose usage the figures cover. */
export function scopeHeading(scope: UsageScope, tenantName: string): { title: string; description: string } {
  if (scope.kind === "tenant") {
    return {
      title: "Usage across the whole organization",
      description: `Every model request made by anyone in ${tenantName}.`,
    };
  }
  if (scope.kind === "departments") {
    const names = scope.departments.map((d) => d.name);
    return {
      title: names.length === 1 ? `Usage in ${names[0]}` : "Usage in your departments",
      description:
        names.length === 0
          ? "Requests by the people in the departments you manage."
          : `Requests by the people in ${joinNames(names)}, ${names.length === 1 ? "the department" : "the departments"} you manage.`,
    };
  }
  return {
    title: "Your own usage",
    description: "Only your own requests. Department managers see their departments, and owners and admins the whole organization.",
  };
}

export const GROUP_LABELS: Record<GroupBy, string> = {
  department: "Department",
  user: "Person",
  agent: "Agent",
  model: "Model",
  feature: "Feature",
};

/**
 * What the breakdown can be grouped by. For your own usage, by person or department would only
 * ever show you, so those are left out.
 */
export function groupOptions(kind: ScopeKind): { value: GroupBy; label: string }[] {
  const keys: GroupBy[] = kind === "self" ? ["feature", "model", "agent"] : ["department", "user", "agent", "model", "feature"];
  return keys.map((value) => ({ value, label: GROUP_LABELS[value] }));
}

export function defaultGroupBy(kind: ScopeKind): GroupBy {
  if (kind === "tenant") return "department";
  if (kind === "departments") return "user";
  return "feature";
}

export const FEATURE_LABELS: Record<UsageFeature, string> = {
  chat: "Assistant",
  agents: "Department agents",
  research: "Research & act",
  workflow: "Workflows",
  meetings: "Meetings",
  analytics: "Insights",
  agent_test: "Agent test console",
  eval: "Evaluations",
  routing: "Request routing",
};

export function featureLabel(feature: string): string {
  return Object.hasOwn(FEATURE_LABELS, feature) ? FEATURE_LABELS[feature as UsageFeature] : feature;
}

/** A group's name as shown: the API's label, with feature keys in words. Never an empty string. */
export function groupLabel(group: Pick<UsageGroup, "key" | "label">, groupBy: GroupBy): string {
  if (groupBy === "feature" && (group.label === group.key || !group.label)) return featureLabel(group.key);
  return group.label || "Unnamed";
}

/** 0.4234 → "42%"; small but non-zero shares read "<1%" rather than "0%". */
export function formatShare(share: number): string {
  if (!Number.isFinite(share) || share <= 0) return "0%";
  if (share < 0.01) return "<1%";
  return `${Math.round(share * 100)}%`;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${formatNumber(n)} ${n === 1 ? one : many}`;
}

export interface Kpi {
  key: "cost" | "requests" | "tokens";
  label: string;
  value: string;
  /** One line under the value; null when there is nothing to add. */
  detail: string | null;
}

/** Why the cost leaves some requests out; null when every request had a price. */
export function unpricedNote(totals: Pick<UsageTotals, "requests" | "unpriced_requests">): string | null {
  const n = totals.unpriced_requests;
  if (n <= 0) return null;
  const which =
    n >= totals.requests
      ? n === 1
        ? "The only request has"
        : `All ${formatNumber(n)} requests have`
      : `${formatNumber(n)} of ${formatNumber(totals.requests)} requests ${n === 1 ? "has" : "have"}`;
  return `${which} no price (a local model, or a model without a price), so the cost leaves ${n === 1 ? "it" : "them"} out.`;
}

/** The three KPI tiles: cost, requests, tokens. Values auto-compact from 10,000 ("12.3K"). */
export function kpis(totals: UsageTotals): Kpi[] {
  const priced = totals.requests - totals.unpriced_requests;
  const tokens = totals.input_tokens + totals.output_tokens;
  return [
    {
      key: "cost",
      label: "Cost",
      value: formatUsd(totals.cost_usd, "compact"),
      detail: totals.unpriced_requests > 0 ? `Excludes ${plural(totals.unpriced_requests, "unpriced request")}` : null,
    },
    {
      key: "requests",
      label: "Requests",
      value: formatNumber(totals.requests, "compact"),
      detail: priced > 0 ? `${formatUsd(totals.cost_usd / priced)} per priced request on average` : null,
    },
    {
      key: "tokens",
      label: "Tokens",
      value: formatNumber(tokens, "compact"),
      detail: tokens > 0 ? `${formatNumber(totals.input_tokens, "compact")} in · ${formatNumber(totals.output_tokens, "compact")} out` : null,
    },
  ];
}

/**
 * This person's own figures from a summary grouped by user: the totals when the scope is only
 * them, otherwise their group (keyed by user id). Not in the groups means no requests.
 */
export function myUsage(
  summary: Pick<UsageSummary, "scope" | "totals" | "groups" | "group_by">,
  meId: string,
): { cost_usd: number; requests: number; unpriced_requests: number | null } {
  if (summary.scope.kind === "self") {
    return { cost_usd: summary.totals.cost_usd, requests: summary.totals.requests, unpriced_requests: summary.totals.unpriced_requests };
  }
  const group = summary.group_by === "user" ? summary.groups.find((g) => g.key === meId) : undefined;
  return { cost_usd: group?.cost_usd ?? 0, requests: group?.requests ?? 0, unpriced_requests: null };
}

// --- Budgets ---------------------------------------------------------------------------------

export const PERIOD_ADJECTIVES: Record<BudgetPeriod, string> = { day: "Daily", month: "Monthly" };
export const PERIOD_NOUNS: Record<BudgetPeriod, string> = { day: "day", month: "month" };

/** "Monthly organization budget", "Daily budget for Finance", "Your monthly cap", "Monthly cap for Alice". */
export function budgetTitle(budget: Pick<BudgetStatus, "scope" | "subject" | "period">, meId: string | null): string {
  const adjective = PERIOD_ADJECTIVES[budget.period] ?? "";
  if (budget.scope === "tenant") return `${adjective} organization budget`;
  if (budget.scope === "department") return `${adjective} budget for ${budget.subject?.name ?? "a department"}`;
  if (!budget.subject || budget.subject.id === meId) return `Your ${adjective.toLowerCase()} cap`;
  return `${adjective} cap for ${budget.subject.name}`;
}

export const STATUS_TEXT: Record<BudgetState, string> = {
  ok: "Within budget",
  warning: "Close to the limit",
  exceeded: "Limit reached",
};

/** Spent as a share of the limit (can pass 1 when a warn-only budget is overspent). */
export function usedShare(budget: Pick<BudgetStatus, "spent_usd" | "limit_usd">): number {
  return budget.limit_usd > 0 ? Math.max(0, budget.spent_usd / budget.limit_usd) : 0;
}

export function percent(share: number): string {
  return `${Math.round(share * 100)}%`;
}

/** "$42.10 of $100 spent (42%)". */
export function spentText(budget: Pick<BudgetStatus, "spent_usd" | "limit_usd">): string {
  return `${formatUsd(budget.spent_usd)} of ${formatUsd(budget.limit_usd)} spent (${percent(usedShare(budget))})`;
}

/** The status in words, with the warning level: never colour alone. */
export function statusDetail(budget: Pick<BudgetStatus, "status" | "warn_at" | "spent_usd" | "limit_usd">): string {
  const warn = percent(budget.warn_at);
  if (budget.status === "exceeded") return `${STATUS_TEXT.exceeded}: ${percent(usedShare(budget))} used`;
  if (budget.status === "warning") return `${STATUS_TEXT.warning}: past the ${warn} warning level`;
  return `${STATUS_TEXT.ok}: warns at ${warn}`;
}

/** "Resets Dec 1, 2026, 1:00 AM (in 19 days)", in the viewer's time zone. */
export function resetText(resetsAt: string, now: Date = new Date()): string {
  const when = formatDateTime(resetsAt);
  if (when === "—") return "Reset time unknown";
  return `Resets ${when} (${formatRelative(resetsAt, now)})`;
}

const STATE_RANK: Record<BudgetState, number> = { exceeded: 2, warning: 1, ok: 0 };

/** Budgets with the most urgent first: exceeded, then warning, then by share used. */
export function sortBudgets<T extends Pick<BudgetStatus, "status" | "spent_usd" | "limit_usd">>(budgets: readonly T[]): T[] {
  return [...budgets].sort((a, b) => STATE_RANK[b.status] - STATE_RANK[a.status] || usedShare(b) - usedShare(a));
}

/**
 * The budgets that limit this person's own requests: the tenant's, the department their usage
 * counts for, and their own cap. The API says which (`applies_to_me`); without it (older API
 * versions) every department of theirs is assumed. An admin's summary also lists everyone
 * else's caps; those don't apply to them.
 */
export function budgetsForMe<T extends Pick<BudgetStatus, "scope" | "subject" | "applies_to_me">>(
  budgets: readonly T[],
  me: Pick<Me, "id" | "departments">,
): T[] {
  const departments = new Set(me.departments.map((d) => d.id));
  return budgets.filter((b) => {
    if (typeof b.applies_to_me === "boolean") return b.applies_to_me;
    if (b.scope === "tenant") return true;
    if (b.scope === "department") return b.subject !== null && departments.has(b.subject.id);
    return b.subject === null || b.subject.id === me.id;
  });
}
