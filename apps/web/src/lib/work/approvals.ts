/**
 * Approval cards, framework-free: what state an approval is in, how its arguments read, the
 * edit form (drafts) and the payload it sends, and the card's state machine. Tested on its own;
 * components/work/approval-card.tsx only renders it.
 *
 * Nothing runs until a person decides: an approval is a stored proposal (docs/automation.md).
 */

import { ApiError } from "@/lib/api";
import { parseValidationDetail, type DetailError } from "@/lib/validation-detail";
import type { Approval, DecisionBody } from "@/lib/work/api";

// --- Status ---------------------------------------------------------------------------------

/** What the card shows. "running": approved, the action is being carried out right now. */
export type DisplayStatus = "pending" | "running" | "executed" | "failed" | "rejected" | "expired" | "superseded";

export function displayStatus(approval: Pick<Approval, "status" | "expires_at">, now: number = Date.now()): DisplayStatus {
  switch (approval.status) {
    case "pending": {
      const expires = Date.parse(approval.expires_at);
      return Number.isFinite(expires) && expires <= now ? "expired" : "pending";
    }
    case "approved":
      return "running";
    default:
      return approval.status;
  }
}

export const STATUS_LABELS: Record<DisplayStatus, string> = {
  pending: "Waiting for approval",
  running: "Approved, running…",
  executed: "Done",
  failed: "Failed",
  rejected: "Rejected",
  expired: "Expired",
  superseded: "Decided by another approver",
};

/** The badge: a copy closed because the request went to someone else says so. */
export function statusLabel(approval: Pick<Approval, "status" | "expires_at" | "result">, now?: number): string {
  const status = displayStatus(approval, now);
  if (status === "superseded" && approval.result?.reason) return "Reassigned";
  return STATUS_LABELS[status];
}

const TERMINAL: ReadonlySet<DisplayStatus> = new Set(["executed", "failed", "rejected", "expired", "superseded"]);

// --- Approval kinds the core carries out (not tool calls) ----------------------------------

/** Approval.tool of a request for official status (modules/knowledge/governance.py). */
export const OFFICIAL_KIND = "knowledge.official_status";

/** Kinds are approved or rejected as they are: their arguments can't be edited. */
export function isEditable(tool: string): boolean {
  return tool !== OFFICIAL_KIND;
}

/** What deciding means, said before the approver decides. */
export function decisionMeaning(tool: string): { approve: string; reject: string } | null {
  if (tool !== OFFICIAL_KIND) return null;
  return {
    approve:
      "Approving makes the document official: answers rank it first and prefer it when sources disagree. It also counts as a review.",
    reject: "Rejecting keeps its current trust level. The person who asked sees your note.",
  };
}

/**
 * An official-status request about a collection the viewer can't open: the API leaves out the
 * document's title and the requester's note (separation of duties, docs/governance.md).
 */
export function isWithheld(approval: Pick<Approval, "tool" | "arguments">): boolean {
  return approval.tool === OFFICIAL_KIND && approval.arguments.withheld === true;
}

/** Where a kind's subject lives, e.g. the document an official-status request is about. */
export function subjectLink(approval: Pick<Approval, "tool" | "arguments">): string | null {
  const documentId = approval.arguments.document_id;
  return approval.tool === OFFICIAL_KIND && typeof documentId === "string" && !isWithheld(approval)
    ? `/knowledge/documents/${encodeURIComponent(documentId)}`
    : null;
}

export function isSettled(approval: Pick<Approval, "status" | "expires_at">, now?: number): boolean {
  return TERMINAL.has(displayStatus(approval, now));
}

/** Only the named approver decides, and only while it's pending and not expired. */
export function canDecide(approval: Approval, userId: string | null | undefined, now?: number): boolean {
  return userId != null && approval.approver_id === userId && displayStatus(approval, now) === "pending";
}

/** One line about the outcome, e.g. "Sent to leadership@acme.test." */
export function outcomeText(approval: Approval, now?: number): string | null {
  switch (displayStatus(approval, now)) {
    case "executed":
      if (approval.tool === OFFICIAL_KIND) return "The document is now official.";
      return approval.result?.content?.trim() || "The action ran.";
    case "failed":
      return approval.result?.error ? `It didn't run: ${approval.result.error}` : "It was approved, but the action failed.";
    case "rejected":
      return approval.decision_note ? `Rejected: “${approval.decision_note}”. Nothing ran.` : "Rejected. Nothing ran.";
    case "expired":
      return "No decision in time. Nothing ran.";
    case "superseded":
      if (approval.result?.reason) return `${approval.result.reason} Nothing more to do.`;
      return approval.tool === OFFICIAL_KIND
        ? "Another approver decided this request first. Nothing more to do."
        : "Another approver decided this first.";
    default:
      return null;
  }
}

// --- Reading the arguments ------------------------------------------------------------------

export const TOOL_TITLES: Record<string, string> = {
  send_email: "Send an email",
  create_task: "Create a task",
  [OFFICIAL_KIND]: "Official status for a document",
};

export function toolTitle(tool: string): string {
  return TOOL_TITLES[tool] ?? `Use ${tool}`;
}

/** The arguments that run (or ran): the approver's edit if there was one. */
export function effectiveArguments(approval: Approval): Record<string, unknown> {
  return approval.edited_arguments ?? approval.arguments;
}

export interface ArgumentRow {
  label: string;
  value: string | string[];
  multiline?: boolean;
}

const text = (value: unknown): string => (typeof value === "string" ? value : value == null ? "" : String(value));
const list = (value: unknown): string[] =>
  Array.isArray(value) ? value.map(text).filter(Boolean) : typeof value === "string" && value ? [value] : [];

/** Readable rows for the tools we know; null means "show the JSON". */
export function argumentRows(tool: string, args: Record<string, unknown>): ArgumentRow[] | null {
  if (tool === "send_email") {
    const rows: ArgumentRow[] = [{ label: "To", value: list(args.to) }];
    if (list(args.cc).length > 0) rows.push({ label: "Cc", value: list(args.cc) });
    rows.push({ label: "Subject", value: text(args.subject) }, { label: "Body", value: text(args.body), multiline: true });
    return rows;
  }
  if (tool === OFFICIAL_KIND) {
    const document =
      args.withheld === true ? `A document in ${text(args.collection) || "a collection"} you can't open` : text(args.title);
    const rows: ArgumentRow[] = [
      { label: "Document", value: document },
      { label: "Collection", value: text(args.collection) },
    ];
    // The request is for this version: new content voids it (docs/governance.md).
    if (typeof args.version === "number") rows.push({ label: "Version", value: `Version ${args.version}` });
    if (text(args.note)) rows.push({ label: "Why", value: text(args.note), multiline: true });
    return rows;
  }
  if (tool === "create_task") {
    const rows: ArgumentRow[] = [{ label: "Title", value: text(args.title) }];
    if (text(args.description)) rows.push({ label: "Description", value: text(args.description), multiline: true });
    rows.push({ label: "Assignee", value: text(args.assignee) || "Nobody yet" });
    if (text(args.department)) rows.push({ label: "Team queue", value: text(args.department) });
    if (text(args.due)) rows.push({ label: "Due", value: text(args.due) });
    return rows;
  }
  return null;
}

/**
 * The assignee row of a proposed task with the colleague's name: the tool takes an email
 * address, which says less than "Wei Zhang (wei.zhang@acme.test)". Unknown addresses stay as they are.
 */
export function withAssigneeName(rows: ArgumentRow[], people: readonly { email: string; display_name: string }[]): ArgumentRow[] {
  const byEmail = new Map(people.map((p) => [p.email.toLowerCase(), p.display_name]));
  return rows.map((row) => {
    if (row.label !== "Assignee" || typeof row.value !== "string") return row;
    const name = byEmail.get(row.value.trim().toLowerCase());
    return name ? { ...row, value: `${name} (${row.value.trim()})` } : row;
  });
}

// --- Editing ------------------------------------------------------------------------------

export interface EmailDraft {
  kind: "email";
  to: string[];
  cc: string[];
  /** Typed but not yet turned into a chip: still counts when approving. */
  toInput: string;
  ccInput: string;
  subject: string;
  body: string;
}

export interface TaskDraft {
  kind: "task";
  title: string;
  description: string;
  assignee: string;
  department: string;
  due: string;
}

export interface JsonDraft {
  kind: "json";
  text: string;
}

export type Draft = EmailDraft | TaskDraft | JsonDraft;

export function draftFor(tool: string, args: Record<string, unknown>): Draft {
  if (tool === "send_email") {
    return { kind: "email", to: list(args.to), cc: list(args.cc), toInput: "", ccInput: "", subject: text(args.subject), body: text(args.body) };
  }
  if (tool === "create_task") {
    return {
      kind: "task",
      title: text(args.title),
      description: text(args.description),
      assignee: text(args.assignee),
      department: text(args.department),
      due: text(args.due),
    };
  }
  return { kind: "json", text: JSON.stringify(args, null, 2) };
}

/** Same shape check as the API (`Email` in modules/work/tools.py): the mail server is the judge. */
export const EMAIL_SHAPE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** "a@x.test, b@x.test; c@x.test" → three addresses. */
export function splitAddresses(input: string): string[] {
  return input
    .split(/[\s,;]+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

/** Adds typed addresses as chips, without duplicates (case-insensitive). */
export function addAddresses(current: readonly string[], input: string): string[] {
  const seen = new Set(current.map((a) => a.toLowerCase()));
  const next = [...current];
  for (const address of splitAddresses(input)) {
    if (seen.has(address.toLowerCase())) continue;
    seen.add(address.toLowerCase());
    next.push(address);
  }
  return next;
}

export type DraftErrors = Partial<Record<string, string>>;

export type EditPayload = { ok: true; arguments: Record<string, unknown> } | { ok: false; errors: DraftErrors };

/** The full edited object for `POST /approvals/{id}/decision`, or what to fix first. */
export function editPayload(draft: Draft): EditPayload {
  const errors: DraftErrors = {};
  if (draft.kind === "email") {
    const to = addAddresses(draft.to, draft.toInput);
    const cc = addAddresses(draft.cc, draft.ccInput);
    const badTo = to.find((a) => !EMAIL_SHAPE.test(a));
    const badCc = cc.find((a) => !EMAIL_SHAPE.test(a));
    if (to.length === 0) errors.to = "Add at least one recipient.";
    else if (badTo) errors.to = `“${badTo}” isn't an email address.`;
    if (badCc) errors.cc = `“${badCc}” isn't an email address.`;
    if (!draft.subject.trim()) errors.subject = "Add a subject.";
    if (!draft.body.trim()) errors.body = "The email needs a body.";
    if (Object.keys(errors).length > 0) return { ok: false, errors };
    return { ok: true, arguments: { to, cc, subject: draft.subject.trim(), body: draft.body } };
  }
  if (draft.kind === "task") {
    if (!draft.title.trim()) errors.title = "Add a title.";
    if (draft.assignee.trim() && !EMAIL_SHAPE.test(draft.assignee.trim())) errors.assignee = "Use the person's email address.";
    if (draft.due && !/^\d{4}-\d{2}-\d{2}$/.test(draft.due)) errors.due = "Pick a date.";
    if (Object.keys(errors).length > 0) return { ok: false, errors };
    return {
      ok: true,
      arguments: {
        title: draft.title.trim(),
        description: draft.description.trim(),
        assignee: draft.assignee.trim() || null,
        department: draft.department.trim() || null,
        due: draft.due || null,
      },
    };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(draft.text);
  } catch (error) {
    return { ok: false, errors: { json: `Not valid JSON: ${error instanceof Error ? error.message : "check the syntax"}` } };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { ok: false, errors: { json: "The arguments must be a JSON object ({ … })." } };
  }
  return { ok: true, arguments: parsed as Record<string, unknown> };
}

// --- The card's state machine ------------------------------------------------------------

export type CardMode = "view" | "edit" | "reject";
export type SubmitKind = "approve" | "edit" | "reject";

export type DecisionError =
  /** 409: someone (or another tab) decided first, or it expired. */
  | { kind: "not_pending"; message: string }
  /** 422: the edited action doesn't fit the tool. */
  | { kind: "invalid_input"; message: string; fields: DetailError[] }
  | { kind: "other"; error: unknown };

export interface CardState {
  mode: CardMode;
  draft: Draft | null;
  /** Shown after a failed local check (before that, the form stays quiet). */
  draftErrors: DraftErrors;
  note: string;
  submitting: SubmitKind | null;
  /** The approval as the last decision returned it. */
  decided: Approval | null;
  error: DecisionError | null;
}

export type CardAction =
  | { type: "edit"; approval: Approval }
  | { type: "reject" }
  | { type: "cancel" }
  | { type: "draft"; draft: Draft }
  | { type: "note"; note: string }
  | { type: "submit"; kind: SubmitKind }
  | { type: "invalid"; errors: DraftErrors }
  | { type: "decided"; approval: Approval }
  | { type: "failed"; error: unknown };

export const initialCardState: CardState = {
  mode: "view",
  draft: null,
  draftErrors: {},
  note: "",
  submitting: null,
  decided: null,
  error: null,
};

export function classifyDecisionError(error: unknown): DecisionError {
  if (error instanceof ApiError && error.code === "not_pending") {
    return { kind: "not_pending", message: "This was already decided or has expired." };
  }
  if (error instanceof ApiError && error.code === "invalid_input") {
    return { kind: "invalid_input", message: "The edited action isn't valid.", fields: parseValidationDetail(error.detail) };
  }
  return { kind: "other", error };
}

export function reduceCard(state: CardState, action: CardAction): CardState {
  // One request at a time: only its outcome (or a failed local check) ends "submitting".
  if (state.submitting && action.type !== "decided" && action.type !== "failed" && action.type !== "invalid") return state;
  switch (action.type) {
    case "edit":
      return {
        ...state,
        mode: "edit",
        draft: draftFor(action.approval.tool, effectiveArguments(action.approval)),
        draftErrors: {},
        error: null,
      };
    case "reject":
      return { ...state, mode: "reject", note: "", error: null };
    case "cancel":
      return { ...state, mode: "view", draft: null, draftErrors: {}, note: "", error: null };
    case "draft":
      return { ...state, draft: action.draft };
    case "note":
      return { ...state, note: action.note };
    case "submit":
      return { ...state, submitting: action.kind, error: null, draftErrors: {} };
    case "invalid":
      return { ...state, submitting: null, draftErrors: action.errors };
    case "decided":
      return { ...initialCardState, decided: action.approval };
    case "failed": {
      const error = classifyDecisionError(action.error);
      // A 409 means the form is moot: back to the (refreshed) card. A 422 keeps the edit open.
      if (error.kind === "not_pending") return { ...initialCardState, decided: state.decided, error };
      return { ...state, submitting: null, error };
    }
  }
}

/** The request for a decision, or local errors to show first. */
export function decisionRequest(state: CardState, kind: SubmitKind): { ok: true; body: DecisionBody } | { ok: false; errors: DraftErrors } {
  if (kind === "approve") return { ok: true, body: { decision: "approve" } };
  if (kind === "reject") {
    const note = state.note.trim();
    return { ok: true, body: note ? { decision: "reject", note } : { decision: "reject" } };
  }
  if (!state.draft) return { ok: false, errors: { form: "Nothing to approve." } };
  const payload = editPayload(state.draft);
  return payload.ok ? { ok: true, body: { decision: "approve", arguments: payload.arguments } } : payload;
}

/** The newest view of an approval: a settled one beats a pending one; otherwise the fetched one. */
export function latestApproval(fetched: Approval, decided: Approval | null): Approval {
  if (!decided || decided.id !== fetched.id) return fetched;
  // A decision's response is never older than a pending copy still in the cache.
  return fetched.status !== "pending" && fetched.status !== "approved" ? fetched : decided;
}

/** Server field errors (422) by form field: "to.0" → "to". */
export function serverFieldErrors(error: DecisionError | null): DraftErrors {
  if (error?.kind !== "invalid_input") return {};
  const out: DraftErrors = {};
  for (const f of error.fields) {
    const key = f.field || "form";
    out[key] = out[key] ? `${out[key]} ${f.message}` : f.message;
  }
  return out;
}

/** Where an approval came from, in words. */
export function originLabel(approval: Approval): string {
  if (approval.origin.via === "workflow") return "From a workflow run";
  if (approval.origin.via === "governance") return "Requested in the Knowledge Hub";
  if (approval.origin.conversation_id) return "From a conversation with the assistant";
  return "Proposed by the assistant";
}
