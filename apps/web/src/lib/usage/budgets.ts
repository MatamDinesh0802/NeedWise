/**
 * The Admin › Budgets form: saved budgets → editable rows → the PUT body, with validation and
 * the API's 422 problems mapped back onto the rows. Framework-free and unit-tested.
 *
 * PUT /admin/budgets replaces everything, so the form always sends every row it holds.
 */

import { ApiError } from "@/lib/api";
import type {
  AdminBudget,
  AdminBudgets,
  BudgetAction,
  BudgetInput,
  BudgetPeriod,
  BudgetScope,
  BudgetsUpdate,
  NamedRef,
} from "@/lib/usage/api";

export const DEFAULT_WARN_PERCENT = 80;

export const PERIOD_OPTIONS: readonly { value: BudgetPeriod; label: string }[] = [
  { value: "month", label: "Per month" },
  { value: "day", label: "Per day" },
];

export const ACTION_OPTIONS: readonly { value: BudgetAction; label: string }[] = [
  { value: "block", label: "Block requests" },
  { value: "warn", label: "Only warn" },
];

export const ACTION_HINTS: Record<BudgetAction, string> = {
  block: "Requests that use a model are refused until it resets.",
  warn: "Requests keep working; people are only told.",
};

export interface BudgetRow {
  /** Stable key for React and errors: the saved id, or "new-<n>". */
  key: string;
  /** The saved budget's id; null until saved. */
  id: string | null;
  scope: BudgetScope;
  /** The department or person; null for the organization budget. */
  subject: NamedRef | null;
  period: BudgetPeriod;
  /** As typed, in US dollars. */
  limit: string;
  /** As typed, 1–100. */
  warnPercent: string;
  action: BudgetAction;
}

export interface DefaultCapForm {
  enabled: boolean;
  period: BudgetPeriod;
  limit: string;
  action: BudgetAction;
}

export interface BudgetsForm {
  rows: BudgetRow[];
  userDefault: DefaultCapForm;
}

/** Field errors keyed "<row key>.<field>" (limit, warn, subject, period) or "default.limit". */
export type FormErrors = Record<string, string>;

function amount(value: number): string {
  return Number.isFinite(value) ? String(Number(value.toFixed(4))) : "";
}

export function rowFromBudget(budget: AdminBudget): BudgetRow {
  return {
    key: budget.id,
    id: budget.id,
    scope: budget.scope,
    subject: budget.subject,
    period: budget.period,
    limit: amount(budget.limit_usd),
    warnPercent: String(Math.round(budget.warn_at * 100)),
    action: budget.action,
  };
}

const SCOPE_ORDER: Record<BudgetScope, number> = { tenant: 0, department: 1, user: 2 };

export function formFromBudgets(data: AdminBudgets): BudgetsForm {
  const rows = [...data.budgets]
    .sort(
      (a, b) =>
        SCOPE_ORDER[a.scope] - SCOPE_ORDER[b.scope] ||
        (a.subject?.name ?? "").localeCompare(b.subject?.name ?? "") ||
        a.period.localeCompare(b.period),
    )
    .map(rowFromBudget);
  const cap = data.user_default;
  return {
    rows,
    userDefault: cap
      ? { enabled: true, period: cap.period, limit: amount(cap.limit_usd), action: cap.action }
      : { enabled: false, period: "month", limit: "", action: "block" },
  };
}

let counter = 0;

export function newRow(scope: BudgetScope, subject: NamedRef | null, period: BudgetPeriod = "month"): BudgetRow {
  counter += 1;
  return { key: `new-${counter}`, id: null, scope, subject, period, limit: "", warnPercent: String(DEFAULT_WARN_PERCENT), action: "block" };
}

/** "12.50" or "$1,200" → 12.5 / 1200; null for anything that isn't a positive amount. */
export function parseLimit(text: string): number | null {
  const cleaned = text.trim().replace(/^\$/, "").replace(/,/g, "");
  if (!/^(\d+\.?\d*|\.\d+)$/.test(cleaned)) return null;
  const value = Number(cleaned);
  return Number.isFinite(value) && value > 0 ? value : null;
}

/** "80" → 0.8; null outside 1–100. */
export function parseWarn(text: string): number | null {
  const cleaned = text.trim().replace(/%$/, "");
  if (!/^\d+(\.\d+)?$/.test(cleaned)) return null;
  const value = Number(cleaned);
  return value >= 1 && value <= 100 ? value / 100 : null;
}

export function rowName(row: Pick<BudgetRow, "scope" | "subject">): string {
  if (row.scope === "tenant") return "the organization";
  return row.subject?.name ?? (row.scope === "department" ? "this department" : "this person");
}

const PERIOD_ADJECTIVE: Record<BudgetPeriod, string> = { day: "daily", month: "monthly" };

export const LIMIT_ERROR = "Enter a limit in US dollars, more than 0.";
export const WARN_ERROR = "Enter a warning level from 1 to 100%.";

export function validateBudgets(form: BudgetsForm): FormErrors {
  const errors: FormErrors = {};
  const seen = new Set<string>();
  for (const row of form.rows) {
    if (parseLimit(row.limit) === null) errors[`${row.key}.limit`] = LIMIT_ERROR;
    if (parseWarn(row.warnPercent) === null) errors[`${row.key}.warn`] = WARN_ERROR;
    if (row.scope !== "tenant" && !row.subject) {
      errors[`${row.key}.subject`] = row.scope === "department" ? "Choose a department." : "Choose a person.";
    }
    const identity = `${row.scope}:${row.subject?.id ?? ""}:${row.period}`;
    if (seen.has(identity)) {
      errors[`${row.key}.period`] = `There is already a ${PERIOD_ADJECTIVE[row.period]} budget for ${rowName(row)}. Change the period or remove one.`;
    }
    seen.add(identity);
  }
  if (form.userDefault.enabled && parseLimit(form.userDefault.limit) === null) errors["default.limit"] = LIMIT_ERROR;
  return errors;
}

/**
 * The PUT body, and which row each entry of `budgets` came from (to place the API's 422 errors).
 * Department rows are left out when `includeDepartments` is false: the API refuses them (402)
 * without the "department_budgets" feature. Call only after validateBudgets found nothing.
 */
export function toUpdate(form: BudgetsForm, { includeDepartments = true }: { includeDepartments?: boolean } = {}): {
  body: BudgetsUpdate;
  order: string[];
} {
  const rows = form.rows.filter((row) => includeDepartments || row.scope !== "department");
  const budgets: BudgetInput[] = rows.map((row) => ({
    scope: row.scope,
    subject_id: row.scope === "tenant" ? null : (row.subject?.id ?? null),
    period: row.period,
    limit_usd: parseLimit(row.limit) ?? 0,
    warn_at: parseWarn(row.warnPercent) ?? DEFAULT_WARN_PERCENT / 100,
    action: row.action,
  }));
  const cap = form.userDefault;
  return {
    body: {
      budgets,
      user_default: cap.enabled ? { period: cap.period, limit_usd: parseLimit(cap.limit) ?? 0, action: cap.action } : null,
    },
    order: rows.map((row) => row.key),
  };
}

/** True when the form differs from what is saved. */
export function isDirty(form: BudgetsForm, saved: AdminBudgets): boolean {
  // A switched-off default cap is the same whatever its hidden fields hold.
  const strip = (f: BudgetsForm) => JSON.stringify({ rows: f.rows, userDefault: f.userDefault.enabled ? f.userDefault : { enabled: false } });
  return strip(form) !== strip(formFromBudgets(saved));
}

const FIELD_FOR: Record<string, string> = { limit_usd: "limit", warn_at: "warn", subject_id: "subject", period: "period", scope: "subject" };

/**
 * A 422 validation_error's FastAPI error list, placed on the rows it is about. Anything that
 * can't be placed is returned in `general`, in words.
 */
export function problemsFromError(error: unknown, order: readonly string[]): { fields: FormErrors; general: string[] } {
  const fields: FormErrors = {};
  const general: string[] = [];
  if (!(error instanceof ApiError) || error.status !== 422) return { fields, general };
  const list = error.problem?.errors;
  if (!Array.isArray(list) || list.length === 0) {
    if (error.detail) general.push(error.detail);
    return { fields, general };
  }
  for (const item of list) {
    if (typeof item !== "object" || item === null) continue;
    const record = item as Record<string, unknown>;
    const message = typeof record.msg === "string" ? record.msg : "Invalid value.";
    const loc = (Array.isArray(record.loc) ? record.loc : []).filter((part) => part !== "body");
    if (loc[0] === "budgets" && typeof loc[1] === "number" && order[loc[1]] && typeof loc[2] === "string" && FIELD_FOR[loc[2]]) {
      fields[`${order[loc[1]]}.${FIELD_FOR[loc[2]]}`] = message;
    } else if (loc[0] === "user_default" && loc[1] === "limit_usd") {
      fields["default.limit"] = message;
    } else {
      general.push(loc.length > 0 ? `${loc.join(" › ")}: ${message}` : message);
    }
  }
  return { fields, general };
}
