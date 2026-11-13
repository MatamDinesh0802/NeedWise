/**
 * Admin › Automation (docs/automation.md): escalation SLAs, approver rules for the assistant's
 * actions, request routing (rules, the model's threshold, triage) and Slack. Shapes mirror
 * apps/api/src/needwise/api/v1/automation.py. Pure helpers, including the approver rule builder;
 * components/admin/automation-section.tsx renders them.
 */

import { apiJson } from "@/lib/api";
import type { NotificationCategory } from "@/lib/work/preferences";

// --- Settings ---------------------------------------------------------------------------------

export type EscalateTo = "backup" | "manager" | "org_admins";

export interface EscalationPolicy {
  enabled: boolean;
  remind_before_hours: number;
  escalate_after_hours: number;
  escalate_to: EscalateTo;
  task_remind_before_hours: number;
  task_escalate_after_hours: number;
  queue_escalate_after_hours: number;
}

export interface RoutingPolicy {
  use_model: boolean;
  /** 0 to 1: the model's confidence a routing needs to count. */
  threshold: number;
  /** A department whose members triage too (org admins always do). */
  triage_department: string | null;
}

export interface AutomationSettings {
  escalation: EscalationPolicy;
  /** Tool name -> approver rule, as written (an email, or an object). */
  approver_rules: Record<string, unknown>;
  routing: RoutingPolicy;
}

export interface RoutingRule {
  id: string;
  department: string;
  department_name: string;
  keywords: string[];
  categories: string[];
  enabled: boolean;
}

export interface RoutingRuleInput {
  department: string;
  keywords: string[];
  categories: string[];
  enabled: boolean;
}

export interface RoutingPreview {
  method: "rule" | "model" | "triage" | "human";
  department: string | null;
  department_name: string | null;
  confidence: number | null;
  matched: string[];
  explanation: string;
}

export interface SlackSettings {
  bot_configured: boolean;
  webhook_configured: boolean;
  channel_categories: NotificationCategory[];
  /** Incoming webhook URLs must start with this (the deployment's SLACK_WEBHOOK_PREFIX). */
  webhook_prefix: string;
}

/** Leave a secret out to keep it; null removes it. */
export interface SlackSave {
  bot_token?: string | null;
  webhook_url?: string | null;
  channel_categories: NotificationCategory[];
}

export const automationApi = {
  get: () => apiJson<AutomationSettings>("/api/v1/admin/automation"),
  save: (body: AutomationSettings) => apiJson<AutomationSettings>("/api/v1/admin/automation", { method: "PUT", json: body }),
  routingRules: () => apiJson<RoutingRule[]>("/api/v1/admin/routing-rules"),
  saveRoutingRules: (rules: RoutingRuleInput[]) =>
    apiJson<RoutingRule[]>("/api/v1/admin/routing-rules", { method: "PUT", json: { rules } }),
  preview: (body: { title: string; description: string; category?: string }) =>
    apiJson<RoutingPreview>("/api/v1/admin/routing/preview", { method: "POST", json: body }),
  slack: () => apiJson<SlackSettings>("/api/v1/admin/slack"),
  saveSlack: (body: SlackSave) => apiJson<SlackSettings>("/api/v1/admin/slack", { method: "PUT", json: body }),
  testSlack: (target: "bot" | "webhook") =>
    apiJson<{ ok: boolean; detail: string }>("/api/v1/admin/slack/test", { method: "POST", json: { target } }),
};

export const automationKeys = {
  all: "admin/automation",
  settings: "admin/automation/settings",
  routingRules: "admin/automation/routing-rules",
  slack: "admin/automation/slack",
};

export const ESCALATE_TO_LABELS: Record<EscalateTo, string> = {
  backup: "Their backup, else manager, else org admins",
  manager: "Their manager, else org admins",
  org_admins: "The org admins",
};

/** Tools the assistant proposes that change something (an admin may type others). */
export const KNOWN_ACTION_TOOLS: readonly { tool: string; label: string }[] = [
  { tool: "send_email", label: "Send an email" },
  { tool: "create_task", label: "Create a task" },
];

// --- Routing rules: keywords and categories as text ------------------------------------------

/** "invoice, refund;  expense report" → ["invoice", "refund", "expense report"]. */
export function splitTerms(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of text.split(/[,;\n]/)) {
    const term = part.trim().replace(/\s+/g, " ");
    if (term && !seen.has(term.toLowerCase())) {
      seen.add(term.toLowerCase());
      out.push(term);
    }
  }
  return out;
}

export interface RuleRow {
  /** Stable key for the list (a saved rule's id, or a new one's). */
  key: string;
  department: string;
  keywords: string;
  categories: string;
  enabled: boolean;
}

export function rowsFrom(rules: readonly RoutingRule[]): RuleRow[] {
  return rules.map((r) => ({
    key: r.id,
    department: r.department,
    keywords: r.keywords.join(", "),
    categories: r.categories.join(", "),
    enabled: r.enabled,
  }));
}

/** Whether the rows differ from the saved rules (their order counts: it is the rules' order). */
export function rowsChanged(rows: readonly RuleRow[], saved: readonly RoutingRule[]): boolean {
  const content = (row: RuleRow) => [row.department, row.keywords, row.categories, row.enabled];
  return JSON.stringify(rows.map(content)) !== JSON.stringify(rowsFrom(saved).map(content));
}

/** The rules to save, or what to fix first (by row index). */
export function rulesToSave(rows: readonly RuleRow[]): { ok: true; rules: RoutingRuleInput[] } | { ok: false; errors: Record<number, string> } {
  const errors: Record<number, string> = {};
  const rules = rows.map((row, index) => {
    const rule = { department: row.department, keywords: splitTerms(row.keywords), categories: splitTerms(row.categories), enabled: row.enabled };
    if (!row.department) errors[index] = "Choose a department.";
    else if (!rule.keywords.length && !rule.categories.length) errors[index] = "Add a keyword or a category.";
    return rule;
  });
  return Object.keys(errors).length ? { ok: false, errors } : { ok: true, rules };
}

// --- Approver rules: a builder for stages of approvers ----------------------------------------

export type SelectorKind = "user" | "org_admins" | "requester_manager" | "department_manager";

export interface SelectorDraft {
  kind: SelectorKind;
  /** An email (user) or a department key (department_manager); "" otherwise. */
  value: string;
}

/** Stages, each any one of its approvers; [] is the default (the person who asked). */
export type RuleDraft = SelectorDraft[][];

export const SELECTOR_LABELS: Record<SelectorKind, string> = {
  user: "A named person",
  org_admins: "Any org admin",
  requester_manager: "A manager of the requester's department",
  department_manager: "A manager of a department",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function selectorDraft(raw: unknown): SelectorDraft | null {
  if (typeof raw === "string") return { kind: "user", value: raw };
  if (!isRecord(raw)) return null;
  const keys = Object.keys(raw);
  if (keys.length !== 1) return null;
  if (typeof raw.user === "string") return { kind: "user", value: raw.user };
  if (raw.role === "org_admins") return { kind: "org_admins", value: "" };
  if (raw.department_manager === "requester") return { kind: "requester_manager", value: "" };
  if (typeof raw.department_manager === "string") return { kind: "department_manager", value: raw.department_manager };
  return null;
}

function stageDraft(raw: unknown): SelectorDraft[] | null {
  if (isRecord(raw) && Array.isArray(raw.any_of) && Object.keys(raw).length === 1) {
    const members = raw.any_of.map(selectorDraft);
    return members.length && members.every((m): m is SelectorDraft => m !== null) ? members : null;
  }
  const one = selectorDraft(raw);
  return one ? [one] : null;
}

/** A rule as the API stores it → the builder's stages; null when the builder can't show it. */
export function ruleToDraft(raw: unknown): RuleDraft | null {
  if (raw === undefined || raw === null) return [];
  if (isRecord(raw) && Array.isArray(raw.chain) && Object.keys(raw).length === 1) {
    const stages = raw.chain.map(stageDraft);
    return stages.length && stages.every((s): s is SelectorDraft[] => s !== null) ? stages : null;
  }
  const stage = stageDraft(raw);
  return stage ? [stage] : null;
}

function selectorRule(selector: SelectorDraft): unknown {
  switch (selector.kind) {
    case "user":
      return selector.value.trim().toLowerCase();
    case "org_admins":
      return { role: "org_admins" };
    case "requester_manager":
      return { department_manager: "requester" };
    case "department_manager":
      return { department_manager: selector.value };
  }
}

function stageRule(stage: readonly SelectorDraft[]): unknown {
  return stage.length === 1 ? selectorRule(stage[0]) : { any_of: stage.map(selectorRule) };
}

/** The builder's stages → the rule the API takes; null for the default (no rule). */
export function draftToRule(draft: RuleDraft): unknown {
  const stages = draft.filter((stage) => stage.length > 0);
  if (stages.length === 0) return null;
  return stages.length === 1 ? stageRule(stages[0]) : { chain: stages.map(stageRule) };
}

/** What still needs filling in, e.g. "Step 2: choose a department". */
export function draftProblems(draft: RuleDraft): string[] {
  const problems: string[] = [];
  draft.forEach((stage, index) => {
    const where = draft.length > 1 ? `Step ${index + 1}: ` : "";
    if (stage.length === 0) problems.push(`${where}add at least one approver, or remove the step.`);
    for (const selector of stage) {
      if (selector.kind === "user" && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(selector.value.trim())) {
        problems.push(`${where}choose a colleague.`);
      }
      if (selector.kind === "department_manager" && !selector.value) problems.push(`${where}choose a department.`);
    }
  });
  return [...new Set(problems)];
}

/** The rule in words, like the API's (modules/work/approvers.py describe). */
export function describeDraft(draft: RuleDraft, departmentName: (key: string) => string = (key) => key): string {
  const stages = draft.filter((stage) => stage.length > 0);
  if (stages.length === 0) return "The person who asked (they confirm their own assistant's action)";
  const selector = (s: SelectorDraft) => {
    if (s.kind === "user") return s.value || "a colleague";
    if (s.kind === "org_admins") return "an org admin";
    if (s.kind === "requester_manager") return "a manager of the requester's department";
    return `a manager of ${s.value ? departmentName(s.value) : "a department"}`;
  };
  return stages.map((stage) => stage.map(selector).join(" or ")).join(", then ");
}
