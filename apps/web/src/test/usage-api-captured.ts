/**
 * Responses of the real usage API, captured on 2026-11-12 from the backend on branch p11-usage
 * (a seeded Acme tenant: Alice $4, Tom $2, Bob $1 and an unpriced call, an evaluation $3; an
 * organization budget of $5 a month, Bob's daily cap of $0.50, a $20 monthly default cap).
 * Ids shortened; nothing else edited. lib/usage/contract.test.ts runs them through the client.
 */

import type { AdminBudgets, UsageEventsPage, UsageSummary } from "@/lib/usage/api";

const tenantBudget = {
  id: "b-tenant",
  scope: "tenant",
  subject: null,
  period: "month",
  limit_usd: 5.0,
  spent_usd: 10.0,
  warn_at: 0.8,
  status: "exceeded",
  resets_at: "2026-12-01T00:00:00Z",
} as const;

const bobCap = {
  id: "b-bob",
  scope: "user",
  subject: { id: "u-bob", name: "Bob Okafor" },
  period: "day",
  limit_usd: 0.5,
  spent_usd: 1.0,
  warn_at: 0.8,
  status: "exceeded",
  resets_at: "2026-11-13T00:00:00Z",
} as const;

export const capturedAdminBudgets: AdminBudgets = {
  budgets: [
    { ...tenantBudget, action: "block" },
    { ...bobCap, action: "block" },
  ],
  user_default: { period: "month", limit_usd: 20.0, action: "warn" },
};

export const capturedAdminSummary: UsageSummary = {
  from: "2026-11-01",
  to: "2026-11-30",
  interval: "day",
  group_by: "department",
  scope: { kind: "tenant", departments: [] },
  totals: { cost_usd: 10.0, input_tokens: 4010, output_tokens: 402, requests: 5, unpriced_requests: 1 },
  series: [{ period: "2026-11-12", cost_usd: 10.0, input_tokens: 4010, output_tokens: 402, requests: 5 }],
  groups: [
    { key: "d-hr", label: "People & HR", cost_usd: 6.0, input_tokens: 2000, output_tokens: 200, requests: 2, share: 0.6 },
    { key: "none", label: "No department", cost_usd: 3.0, input_tokens: 1000, output_tokens: 100, requests: 1, share: 0.3 },
    { key: "d-eng", label: "Engineering", cost_usd: 1.0, input_tokens: 1010, output_tokens: 102, requests: 2, share: 0.1 },
  ],
  budgets: [
    tenantBudget,
    bobCap,
    {
      id: "b-default",
      scope: "user",
      subject: { id: "u-erin", name: "Erin Takahashi" },
      period: "month",
      limit_usd: 20.0,
      spent_usd: 0.0,
      warn_at: 0.8,
      status: "ok",
      resets_at: "2026-12-01T00:00:00Z",
    },
  ],
};

export const capturedManagerSummary: UsageSummary = {
  from: "2026-11-01",
  to: "2026-11-30",
  interval: "day",
  group_by: "user",
  scope: { kind: "departments", departments: [{ id: "d-hr", name: "People & HR" }] },
  totals: { cost_usd: 6.0, input_tokens: 2000, output_tokens: 200, requests: 2, unpriced_requests: 0 },
  series: [{ period: "2026-11-12", cost_usd: 6.0, input_tokens: 2000, output_tokens: 200, requests: 2 }],
  groups: [
    { key: "u-alice", label: "Alice Moreau", cost_usd: 4.0, input_tokens: 1000, output_tokens: 100, requests: 1, share: 0.666667 },
    { key: "u-tom", label: "Tom Becker", cost_usd: 2.0, input_tokens: 1000, output_tokens: 100, requests: 1, share: 0.333333 },
  ],
  budgets: [
    tenantBudget,
    {
      id: "b-default",
      scope: "user",
      subject: { id: "u-alice", name: "Alice Moreau" },
      period: "month",
      limit_usd: 20.0,
      spent_usd: 4.0,
      warn_at: 0.8,
      status: "ok",
      resets_at: "2026-12-01T00:00:00Z",
    },
  ],
};

export const capturedMemberSummary: UsageSummary = {
  from: "2026-11-01",
  to: "2026-11-30",
  interval: "day",
  group_by: "feature",
  scope: { kind: "self", departments: [] },
  totals: { cost_usd: 1.0, input_tokens: 1010, output_tokens: 102, requests: 2, unpriced_requests: 1 },
  series: [{ period: "2026-11-12", cost_usd: 1.0, input_tokens: 1010, output_tokens: 102, requests: 2 }],
  groups: [
    { key: "research", label: "research", cost_usd: 1.0, input_tokens: 1000, output_tokens: 100, requests: 1, share: 1.0 },
    { key: "chat", label: "chat", cost_usd: 0.0, input_tokens: 10, output_tokens: 2, requests: 1, share: 0.0 },
  ],
  budgets: [tenantBudget, bobCap],
};

export const capturedEvents: UsageEventsPage = {
  items: [
    {
      id: "e-2",
      occurred_at: "2026-11-12T15:01:51.608402+00:00",
      user: { id: "u-bob", name: "Bob Okafor" },
      department: { id: "d-eng", name: "Engineering" },
      agent_key: null,
      feature: "chat",
      model: "llama3",
      provider: "anthropic",
      local: false,
      input_tokens: 10,
      output_tokens: 2,
      cost_usd: null,
      ref: null,
    },
    {
      id: "e-1",
      occurred_at: "2026-11-12T15:01:51.604707+00:00",
      user: null,
      department: null,
      agent_key: null,
      feature: "eval",
      model: "claude-sonnet-5-5",
      provider: "anthropic",
      local: false,
      input_tokens: 1000,
      output_tokens: 100,
      cost_usd: 3.0,
      ref: null,
    },
  ],
  next_cursor: "2026-11-12T15:01:51.604707+00:00|e-1",
};

/** POST /conversations/{id}/messages over the organization budget (429). */
export const capturedBudgetExceeded = {
  type: "about:blank",
  title: "Budget exceeded",
  status: 429,
  code: "budget_exceeded",
  detail:
    "The monthly organization budget of $5.00 is used up ($10.00 spent). Requests that use an AI model are paused until it resets at 2026-12-01 00:00 UTC.",
  budget: tenantBudget,
};

/** The run.failed event of a chat run stopped by that budget (same detail and budget). */
export const capturedRunFailed = {
  code: "budget_exceeded",
  message: capturedBudgetExceeded.detail,
  detail: capturedBudgetExceeded.detail,
  budget: tenantBudget,
  message_id: "m-1",
};

/** PUT /admin/budgets with a limit of 0 (422, FastAPI's error list). */
export const capturedInvalidBudgets = {
  type: "about:blank",
  title: "Invalid request",
  status: 422,
  code: "validation_error",
  errors: [
    { type: "greater_than", loc: ["body", "budgets", 0, "limit_usd"], msg: "Input should be greater than 0", input: 0, ctx: { gt: 0.0 } },
  ],
};

/** GET /usage/export.csv without the chargeback_reports feature (402). */
export const capturedChargebackUnlicensed = {
  type: "about:blank",
  title: "Enterprise feature",
  status: 402,
  code: "enterprise_feature",
  detail:
    "Chargeback reports ('chargeback_reports') is part of NeedWise Enterprise and is not licensed on this deployment. To enable it, install a license that includes it: see docs/enterprise.md.",
};
