/**
 * Retention policies (Enterprise feature "retention_policies", ee/python/src/workmind_ee/retention.py,
 * docs/enterprise.md). How many days each kind of data is kept; empty keeps it forever. The API
 * enforces the license (402), the 30-day minimum and who may change it; this only explains.
 */

import { apiJson } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { PersonRef } from "@/lib/admin/api";

export type RetentionKind = "conversations" | "analytics_runs" | "meeting_recordings" | "read_notifications";

export const RETENTION_MIN_DAYS = 30;
export const RETENTION_MAX_DAYS = 36500;
/** What the run dialog asks the admin to type (RunIn.confirm). */
export const CONFIRM_WORD = "delete";

export const RETENTION_KINDS: readonly { kind: RetentionKind; label: string; description: string; one: string; many: string }[] = [
  {
    kind: "conversations",
    label: "Conversations",
    description: "Counted from the last message. Their messages are deleted with them.",
    one: "conversation",
    many: "conversations",
  },
  {
    kind: "analytics_runs",
    label: "Insights questions",
    description: "BI agent runs: the question, the SQL and the answer. Counted from when they were asked.",
    one: "Insights question",
    many: "Insights questions",
  },
  {
    kind: "meeting_recordings",
    label: "Meeting recordings",
    description: "The audio or video file only. The meeting, its transcript, summary, decisions and action items stay.",
    one: "meeting recording",
    many: "meeting recordings",
  },
  {
    kind: "read_notifications",
    label: "Read notifications",
    description: "Counted from when they were read. Unread notifications are never deleted.",
    one: "read notification",
    many: "read notifications",
  },
];

export type RetentionCounts = Record<RetentionKind, number>;

export interface RetentionPolicyBody {
  conversations_days: number | null;
  analytics_runs_days: number | null;
  meeting_recordings_days: number | null;
  read_notifications_days: number | null;
}

export interface RetentionPolicy extends RetentionPolicyBody {
  minimum_days: number;
  /** null: never saved (everything is kept). */
  updated_at: string | null;
  updated_by: PersonRef | null;
  last_run_at: string | null;
  last_run: { trigger?: "manual" | "schedule"; deleted?: Partial<RetentionCounts>; files?: number };
}

export interface RetentionPreview {
  as_of: string;
  /** Items older than this are deleted; null: kept forever. */
  cutoffs: Record<RetentionKind, string | null>;
  would_delete: RetentionCounts;
  /** Expired, but a legal hold keeps them (docs/enterprise.md#legal-hold). */
  kept_under_hold?: RetentionCounts;
}

export interface RetentionRun {
  as_of: string;
  deleted: RetentionCounts;
  files_deleted: number;
  kept_under_hold?: RetentionCounts;
}

const BASE = "/api/v1/admin/retention";

export const retentionApi = {
  get: () => apiJson<RetentionPolicy>(BASE),
  save: (body: RetentionPolicyBody) => apiJson<RetentionPolicy>(BASE, { method: "PUT", json: body }),
  /** With a body: what that draft would delete now, without saving it. */
  preview: (body?: RetentionPolicyBody) =>
    apiJson<RetentionPreview>(`${BASE}/preview`, body ? { method: "POST", json: body } : { method: "POST" }),
  run: () => apiJson<RetentionRun>(`${BASE}/run`, { method: "POST", json: { confirm: CONFIRM_WORD } }),
};

export const retentionKeys = {
  policy: "admin/retention",
};

// --- The form ----------------------------------------------------------------------------------

export type RetentionDraft = Record<RetentionKind, string>;
export type RetentionDraftErrors = Partial<Record<RetentionKind, string>>;

const field = (kind: RetentionKind) => `${kind}_days` as const;

export function draftFromPolicy(policy: RetentionPolicyBody | null): RetentionDraft {
  const draft = {} as RetentionDraft;
  for (const { kind } of RETENTION_KINDS) draft[kind] = policy?.[field(kind)]?.toString() ?? "";
  return draft;
}

export function draftErrors(draft: RetentionDraft, minimum: number = RETENTION_MIN_DAYS): RetentionDraftErrors {
  const errors: RetentionDraftErrors = {};
  for (const { kind } of RETENTION_KINDS) {
    const text = draft[kind].trim();
    if (text === "") continue;
    if (!/^\d+$/.test(text)) errors[kind] = "Enter a whole number of days, or leave it empty to keep everything.";
    else if (Number(text) < minimum) errors[kind] = `At least ${minimum} days.`;
    else if (Number(text) > RETENTION_MAX_DAYS) errors[kind] = `At most ${RETENTION_MAX_DAYS.toLocaleString("en-US")} days.`;
  }
  return errors;
}

/** Call only when draftErrors() is empty. */
export function draftToBody(draft: RetentionDraft): RetentionPolicyBody {
  const body = {} as RetentionPolicyBody;
  for (const { kind } of RETENTION_KINDS) {
    const text = draft[kind].trim();
    body[field(kind)] = text === "" ? null : Number(text);
  }
  return body;
}

/** Whether the form differs from what is saved (blank and "keep forever" are the same). */
export function draftChanged(draft: RetentionDraft, saved: RetentionPolicyBody | null): boolean {
  const original = draftFromPolicy(saved);
  return RETENTION_KINDS.some(({ kind }) => draft[kind].trim() !== original[kind]);
}

/** Nothing would ever be deleted: every kind is kept forever. */
export function keepsEverything(policy: RetentionPolicyBody | null): boolean {
  return !policy || RETENTION_KINDS.every(({ kind }) => policy[field(kind)] == null);
}

export function countText(kind: RetentionKind, n: number): string {
  const info = RETENTION_KINDS.find((k) => k.kind === kind)!;
  return `${n.toLocaleString("en-US")} ${n === 1 ? info.one : info.many}`;
}

/** "2 conversations and 1 read notification", or "nothing". */
export function countsSummary(counts: Partial<RetentionCounts> | undefined): string {
  const parts = RETENTION_KINDS.filter(({ kind }) => (counts?.[kind] ?? 0) > 0).map(({ kind }) => countText(kind, counts![kind]!));
  if (parts.length === 0) return "nothing";
  if (parts.length === 1) return parts[0];
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

export function lastRunSummary(policy: Pick<RetentionPolicy, "last_run_at" | "last_run">): string {
  if (!policy.last_run_at) return "It has not run yet. The daily task applies the saved policy once a day.";
  const how = policy.last_run.trigger === "manual" ? "run by hand" : "the daily run";
  return `Last run ${formatDateTime(policy.last_run_at)} (${how}): deleted ${countsSummary(policy.last_run.deleted)}.`;
}

/** Whether a legal hold kept anything that had expired. */
export function anyKept(counts: Partial<RetentionCounts> | undefined): boolean {
  return RETENTION_KINDS.some(({ kind }) => (counts?.[kind] ?? 0) > 0);
}

export function confirmMatches(text: string): boolean {
  return text.trim() === CONFIRM_WORD;
}

// --- Documents, per collection (ee/python/src/workmind_ee/document_retention.py) ------------------

export type DocumentAction = "archive" | "delete";

export const DOCUMENT_ACTIONS: readonly { value: DocumentAction; label: string }[] = [
  { value: "archive", label: "Archive (out of search, kept)" },
  { value: "delete", label: "Delete" },
];

export interface DocumentRule {
  collection_id: string;
  days: number;
  action: DocumentAction;
}

export interface DocumentKept {
  legal_hold: number;
  official: number;
  meeting_transcript: number;
  synced: number;
}

export interface DocumentRuleOut extends DocumentRule {
  collection: string;
  updated_at: string;
  updated_by: PersonRef | null;
  last_run_at: string | null;
  last_run: { trigger?: "manual" | "schedule"; action?: DocumentAction; count?: number; kept?: Partial<DocumentKept> };
}

export interface DocumentRules {
  minimum_days: number;
  rules: DocumentRuleOut[];
}

export interface DocumentOutcome extends DocumentRule {
  collection: string;
  /** Documents not updated since this are deleted or archived. */
  cutoff: string;
  /** Deleted or archived (in a preview: would be). */
  count: number;
  kept: DocumentKept;
}

export interface DocumentPreview {
  as_of: string;
  collections: DocumentOutcome[];
  would_delete: number;
  would_archive: number;
  kept: DocumentKept;
}

export interface DocumentRun {
  as_of: string;
  collections: DocumentOutcome[];
  deleted: number;
  archived: number;
  kept: DocumentKept;
  files_deleted: number;
}

const DOCUMENTS = `${BASE}/documents`;

export const documentRetentionApi = {
  get: () => apiJson<DocumentRules>(DOCUMENTS),
  save: (rules: DocumentRule[]) => apiJson<DocumentRules>(DOCUMENTS, { method: "PUT", json: { rules } }),
  /** With rules: what that draft would do now, without saving it. */
  preview: (rules?: DocumentRule[]) =>
    apiJson<DocumentPreview>(`${DOCUMENTS}/preview`, rules ? { method: "POST", json: { rules } } : { method: "POST" }),
  run: () => apiJson<DocumentRun>(`${DOCUMENTS}/run`, { method: "POST", json: { confirm: CONFIRM_WORD } }),
};

export const documentRetentionKeys = {
  rules: "admin/retention/documents",
};

/** One row per collection: days (empty: keep forever) and what happens. */
export type DocumentDraft = Record<string, { days: string; action: DocumentAction }>;

export function documentDraftFromRules(rules: readonly DocumentRule[], collectionIds: readonly string[]): DocumentDraft {
  const draft: DocumentDraft = {};
  for (const id of collectionIds) draft[id] = { days: "", action: "archive" };
  for (const rule of rules) draft[rule.collection_id] = { days: String(rule.days), action: rule.action };
  return draft;
}

export function documentDraftErrors(draft: DocumentDraft, minimum: number = RETENTION_MIN_DAYS): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const [id, row] of Object.entries(draft)) {
    const text = row.days.trim();
    if (text === "") continue;
    if (!/^\d+$/.test(text)) errors[id] = "Enter a whole number of days, or leave it empty to keep every document.";
    else if (Number(text) < minimum) errors[id] = `At least ${minimum} days.`;
    else if (Number(text) > RETENTION_MAX_DAYS) errors[id] = `At most ${RETENTION_MAX_DAYS.toLocaleString("en-US")} days.`;
  }
  return errors;
}

/** Call only when documentDraftErrors() is empty. Collections left empty get no rule. */
export function documentDraftToRules(draft: DocumentDraft): DocumentRule[] {
  return Object.entries(draft)
    .filter(([, row]) => row.days.trim() !== "")
    .map(([collection_id, row]) => ({ collection_id, days: Number(row.days.trim()), action: row.action }));
}

export function documentDraftChanged(draft: DocumentDraft, saved: readonly DocumentRule[]): boolean {
  const key = (rules: readonly DocumentRule[]) =>
    rules
      .map((r) => `${r.collection_id}:${r.days}:${r.action}`)
      .sort()
      .join("|");
  return key(documentDraftToRules(draft)) !== key(saved);
}

const KEPT_REASONS: readonly { key: keyof DocumentKept; one: string; many: string }[] = [
  { key: "legal_hold", one: "under a legal hold", many: "under a legal hold" },
  { key: "official", one: "official or awaiting approval", many: "official or awaiting approval" },
  { key: "meeting_transcript", one: "a meeting transcript", many: "meeting transcripts" },
  { key: "synced", one: "synced from a source", many: "synced from a source" },
];

/** "2 under a legal hold and 1 official or awaiting approval", or null when nothing was kept. */
export function keptSummary(kept: Partial<DocumentKept> | undefined): string | null {
  const parts = KEPT_REASONS.filter(({ key }) => (kept?.[key] ?? 0) > 0).map(
    ({ key, one, many }) => `${kept![key]!.toLocaleString("en-US")} ${kept![key] === 1 ? one : many}`,
  );
  if (parts.length === 0) return null;
  if (parts.length === 1) return parts[0];
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

export function documentCount(n: number): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? "document" : "documents"}`;
}
