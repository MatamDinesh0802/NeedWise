/**
 * Typed client for usage analytics and budgets (Phase 11): /api/v1/usage/* and
 * /api/v1/admin/budgets. Shapes follow the usage and budgets API contract.
 * Everything goes through the shared wrapper (CSRF header, problem+json errors as ApiError).
 *
 * The API decides what each caller sees: org owners and admins the whole tenant, department
 * managers their departments, everyone else only their own usage. Nothing here widens that.
 */

import { apiFetch, apiJson } from "@/lib/api";

// --- Summary ---------------------------------------------------------------------------------

export type GroupBy = "department" | "user" | "agent" | "model" | "feature";
export type Interval = "day" | "week" | "month";
export type UsageFeature = "chat" | "agents" | "research" | "workflow" | "meetings" | "analytics" | "agent_test" | "eval";

export const GROUP_BYS: readonly GroupBy[] = ["department", "user", "agent", "model", "feature"];
export const USAGE_FEATURES: readonly UsageFeature[] = ["chat", "agents", "research", "workflow", "meetings", "analytics", "agent_test", "eval"];

export interface NamedRef {
  id: string;
  name: string;
}

export type ScopeKind = "tenant" | "departments" | "self";

export interface UsageScope {
  kind: ScopeKind;
  /** The departments a manager sees; empty for the other kinds. */
  departments: NamedRef[];
}

export interface UsageTotals {
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  requests: number;
  /** Requests whose model has no price (local models, unknown models): not in cost_usd. */
  unpriced_requests: number;
}

export interface UsagePoint {
  /** Start of the period, YYYY-MM-DD. */
  period: string;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  requests: number;
}

export interface UsageGroup {
  /** An id (department, user), an agent key, a model or a feature. */
  key: string;
  /** What people read: a name, never an id. */
  label: string;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  requests: number;
  /** Share of the cost, 0…1. Groups arrive sorted by cost, largest first. */
  share: number;
}

export type BudgetScope = "tenant" | "department" | "user";
export type BudgetPeriod = "day" | "month";
export type BudgetState = "ok" | "warning" | "exceeded";
export type BudgetAction = "block" | "warn";

export interface BudgetStatus {
  id: string;
  scope: BudgetScope;
  /** The department or person; null for the tenant budget. */
  subject: NamedRef | null;
  period: BudgetPeriod;
  limit_usd: number;
  spent_usd: number;
  /** Warn once this share of the limit is spent, 0…1. */
  warn_at: number;
  status: BudgetState;
  /** ISO 8601 date-time. */
  resets_at: string;
  /**
   * Usage summary only: whether it limits the caller's own requests. A person in two departments
   * sees both departments' budgets; only the one their usage counts for limits them. Missing
   * from older API versions.
   */
  applies_to_me?: boolean;
}

export interface UsageSummary {
  from: string;
  to: string;
  interval: Interval;
  group_by: GroupBy;
  scope: UsageScope;
  totals: UsageTotals;
  series: UsagePoint[];
  groups: UsageGroup[];
  /** Budget status the caller may see. */
  budgets: BudgetStatus[];
}

export interface SummaryParams {
  from: string;
  to: string;
  group_by: GroupBy;
  interval: Interval;
}

// --- Events (admin) --------------------------------------------------------------------------

export interface UsageEvent {
  id: string;
  occurred_at: string;
  user: NamedRef | null;
  department: NamedRef | null;
  agent_key: string | null;
  feature: UsageFeature | string;
  model: string;
  provider: string;
  local: boolean;
  input_tokens: number;
  output_tokens: number;
  /** Null when the model has no price. */
  cost_usd: number | null;
  ref: { type: string; id: string } | null;
}

export interface UsageEventsPage {
  items: UsageEvent[];
  next_cursor: string | null;
}

export interface EventFilters {
  from: string;
  to: string;
  user_id?: string | null;
  department_id?: string | null;
  agent?: string | null;
  model?: string | null;
  feature?: string | null;
}

// --- Budgets (admin) -------------------------------------------------------------------------

export interface AdminBudget extends BudgetStatus {
  action: BudgetAction;
}

export interface UserDefaultCap {
  period: BudgetPeriod;
  limit_usd: number;
  action: BudgetAction;
  /** 0–1. Missing from API versions before it was returned (they kept it, and used 0.8). */
  warn_at?: number;
}

export interface AdminBudgets {
  budgets: AdminBudget[];
  /** The cap for everyone without a cap of their own; null when there is none. */
  user_default: UserDefaultCap | null;
}

export interface BudgetInput {
  scope: BudgetScope;
  /** Department or user id; null for the tenant budget. */
  subject_id: string | null;
  period: BudgetPeriod;
  limit_usd: number;
  warn_at: number;
  action: BudgetAction;
}

export interface BudgetsUpdate {
  budgets: BudgetInput[];
  user_default: UserDefaultCap | null;
}

// --- Client ----------------------------------------------------------------------------------

/** Builds a query string in the given order, skipping empty values. */
export function usageQuery(params: Record<string, string | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, value);
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}

const BASE = "/api/v1/usage";
const BUDGETS = "/api/v1/admin/budgets";

export function summaryPath(params: SummaryParams): string {
  return `${BASE}/summary${usageQuery({ from: params.from, to: params.to, group_by: params.group_by, interval: params.interval })}`;
}

export function eventsPath(filters: EventFilters, cursor: string | null = null): string {
  return `${BASE}/events${usageQuery({
    from: filters.from,
    to: filters.to,
    user_id: filters.user_id,
    department_id: filters.department_id,
    agent: filters.agent,
    model: filters.model,
    feature: filters.feature,
    cursor,
  })}`;
}

/** The chargeback report: cost per department for the range (Enterprise: "chargeback_reports"). */
export function chargebackPath(range: { from: string; to: string }): string {
  return `${BASE}/export.csv${usageQuery({ from: range.from, to: range.to, group_by: "department" })}`;
}

export const usageApi = {
  summary: (params: SummaryParams) => apiJson<UsageSummary>(summaryPath(params)),
  /** Owners and admins. Newest first; pass `next_cursor` back for the next page. */
  events: (filters: EventFilters, cursor: string | null = null) => apiJson<UsageEventsPage>(eventsPath(filters, cursor)),
  /** The CSV as a Blob. 402 "enterprise_feature" without a license that includes chargeback reports. */
  chargebackCsv: async (range: { from: string; to: string }) => {
    const response = await apiFetch(chargebackPath(range), { headers: { Accept: "text/csv" } });
    return response.blob();
  },

  // Administration (owner/admin)
  budgets: () => apiJson<AdminBudgets>(BUDGETS),
  /**
   * Replaces every budget and the default cap. 402 "enterprise_feature" when a department budget
   * is included without the "department_budgets" feature; 422 on invalid input.
   */
  saveBudgets: (body: BudgetsUpdate) => apiJson<AdminBudgets>(BUDGETS, { method: "PUT", json: body }),
};

/** Query-cache keys: every input of the fetcher is in the key. */
export const usageKeys = {
  all: "usage/",
  summary: (params: SummaryParams) => `usage/summary?${params.from}&${params.to}&${params.group_by}&${params.interval}`,
  events: (filters: EventFilters) => `usage/events${eventsPath(filters).slice(`${BASE}/events`.length)}`,
  budgets: "usage/admin/budgets",
};
