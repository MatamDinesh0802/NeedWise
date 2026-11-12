/** Usage and budget responses shaped like the Phase 11 usage API contract. */

import type { AdminBudgets, BudgetStatus, UsageSummary } from "@/lib/usage/api";

export const tenantBudget: BudgetStatus = {
  id: "b-tenant",
  scope: "tenant",
  subject: null,
  period: "month",
  limit_usd: 500,
  spent_usd: 212.4,
  warn_at: 0.8,
  status: "ok",
  resets_at: "2026-12-01T00:00:00Z",
};

export const financeBudget: BudgetStatus = {
  id: "b-finance",
  scope: "department",
  subject: { id: "d-finance", name: "Finance" },
  period: "month",
  limit_usd: 100,
  spent_usd: 86,
  warn_at: 0.8,
  status: "warning",
  resets_at: "2026-12-01T00:00:00Z",
};

export const aliceCap: BudgetStatus = {
  id: "b-alice",
  scope: "user",
  subject: { id: "0190f2c4-0000-7000-8000-000000000001", name: "Alice Anders" },
  period: "day",
  limit_usd: 2,
  spent_usd: 2.13,
  warn_at: 0.8,
  status: "exceeded",
  resets_at: "2026-11-13T00:00:00Z",
};

export const tenantSummary: UsageSummary = {
  from: "2026-11-01",
  to: "2026-11-05",
  interval: "day",
  group_by: "department",
  scope: { kind: "tenant", departments: [] },
  totals: { cost_usd: 18.75, input_tokens: 1_250_000, output_tokens: 310_000, requests: 640, unpriced_requests: 40 },
  series: [
    { period: "2026-11-01", cost_usd: 3.5, input_tokens: 200_000, output_tokens: 50_000, requests: 110 },
    { period: "2026-11-02", cost_usd: 4.25, input_tokens: 260_000, output_tokens: 60_000, requests: 130 },
    // 2026-11-03: nothing that day
    { period: "2026-11-04", cost_usd: 6, input_tokens: 400_000, output_tokens: 100_000, requests: 200 },
    { period: "2026-11-05", cost_usd: 5, input_tokens: 390_000, output_tokens: 100_000, requests: 200 },
  ],
  groups: [
    { key: "d-eng", label: "Engineering", cost_usd: 9.5, input_tokens: 700_000, output_tokens: 160_000, requests: 300, share: 0.5067 },
    { key: "d-finance", label: "Finance", cost_usd: 6.25, input_tokens: 400_000, output_tokens: 100_000, requests: 220, share: 0.3333 },
    { key: "d-hr", label: "People & HR", cost_usd: 3, input_tokens: 150_000, output_tokens: 50_000, requests: 120, share: 0.16 },
  ],
  budgets: [tenantBudget, financeBudget],
};

export const selfSummary: UsageSummary = {
  ...tenantSummary,
  group_by: "feature",
  scope: { kind: "self", departments: [] },
  totals: { cost_usd: 1.2, input_tokens: 40_000, output_tokens: 9_000, requests: 31, unpriced_requests: 0 },
  groups: [
    { key: "chat", label: "chat", cost_usd: 0.9, input_tokens: 30_000, output_tokens: 7_000, requests: 25, share: 0.75 },
    { key: "analytics", label: "analytics", cost_usd: 0.3, input_tokens: 10_000, output_tokens: 2_000, requests: 6, share: 0.25 },
  ],
  budgets: [aliceCap],
};

export const adminBudgets: AdminBudgets = {
  budgets: [
    { ...aliceCap, action: "block" },
    { ...financeBudget, action: "warn" },
    { ...tenantBudget, action: "block" },
  ],
  user_default: { period: "month", limit_usd: 25, action: "block" },
};
