/**
 * Legal holds (Enterprise feature "legal_hold", ee/python/src/workmind_ee/legal_hold.py,
 * docs/enterprise.md#legal-hold). Owners and admins place a hold on a document, a collection, a
 * person, a meeting or an Insights question; while it is active nothing it covers can be deleted.
 * The API enforces the license (402), who may place or release a hold, and the holds themselves on
 * every deletion path; this only explains.
 */

import { apiJson } from "@/lib/api";
import type { PersonRef } from "@/lib/admin/api";

export type HoldTargetType = "document" | "collection" | "user" | "meeting" | "analytics_run";
export type HoldStatus = "active" | "released" | "all";

/** How the form finds what to hold: a list of collections, a person search, or a pasted id or link. */
export type TargetPicker = "collection" | "person" | "id";

export const HOLD_TARGETS: readonly {
  type: HoldTargetType;
  label: string;
  keeps: string;
  picker: TargetPicker;
  /** Where its id shows up, for the "id or link" field. */
  where?: string;
}[] = [
  {
    type: "user",
    label: "A person",
    keeps: "Their conversations, the meetings they uploaded and their Insights questions, including new ones.",
    picker: "person",
  },
  {
    type: "collection",
    label: "A collection",
    keeps: "Every document and meeting in it, including new ones.",
    picker: "collection",
  },
  {
    type: "document",
    label: "A document",
    keeps: "That document (and, for a meeting transcript, its meeting).",
    picker: "id",
    where: "the document's page: /knowledge/documents/…",
  },
  {
    type: "meeting",
    label: "A meeting",
    keeps: "That meeting and its transcript.",
    picker: "id",
    where: "the meeting's page: /meetings/…",
  },
  {
    type: "analytics_run",
    label: "An Insights question",
    keeps: "That question, its SQL and its answer.",
    picker: "id",
    where: "the question in Insights: /insights?run=…",
  },
];

export interface HoldCovers {
  documents: number;
  conversations: number;
  meetings: number;
  analytics_runs: number;
}

export interface LegalHold {
  id: string;
  matter: string;
  reason: string;
  target: {
    type: HoldTargetType;
    id: string;
    /** Never content the reader can't open: no question text, no titles from collections they can't open. */
    label: string;
    exists: boolean;
  };
  covers: HoldCovers;
  placed_by: PersonRef | null;
  placed_at: string;
  active: boolean;
  released_at: string | null;
  released_by: PersonRef | null;
  release_reason: string | null;
}

export interface HoldIn {
  matter: string;
  reason: string;
  target_type: HoldTargetType;
  target_id: string;
}

const BASE = "/api/v1/admin/legal-holds";

export const legalHoldsApi = {
  list: (status: HoldStatus = "active") => apiJson<{ items: LegalHold[] }>(`${BASE}?status=${status}`),
  place: (body: HoldIn) => apiJson<LegalHold>(BASE, { method: "POST", json: body }),
  release: (id: string, reason: string) =>
    apiJson<LegalHold>(`${BASE}/${encodeURIComponent(id)}/release`, { method: "POST", json: { reason } }),
};

export const legalHoldKeys = {
  list: (status: HoldStatus) => `admin/legal-holds/${status}`,
  all: "admin/legal-holds/",
};

export const MATTER_MAX = 200;
export const REASON_MAX = 2000;

const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

/** The id in what was pasted: the id itself, or a link to the item's page. */
export function parseTargetId(text: string): string | null {
  const found = text.trim().match(UUID);
  return found ? found[0].toLowerCase() : null;
}

export function targetInfo(type: HoldTargetType) {
  return HOLD_TARGETS.find((t) => t.type === type)!;
}

export interface HoldDraft {
  matter: string;
  reason: string;
  target_type: HoldTargetType;
  /** For collections and people: the picked id. For the others: what was typed or pasted. */
  target: string;
}

export type HoldDraftErrors = Partial<Record<"matter" | "reason" | "target", string>>;

export function emptyHoldDraft(type: HoldTargetType = "user"): HoldDraft {
  return { matter: "", reason: "", target_type: type, target: "" };
}

export function holdDraftErrors(draft: HoldDraft): HoldDraftErrors {
  const errors: HoldDraftErrors = {};
  const matter = draft.matter.trim();
  const reason = draft.reason.trim();
  if (!matter) errors.matter = "Name the matter, e.g. “Smith v. Acme”.";
  else if (matter.length > MATTER_MAX) errors.matter = `At most ${MATTER_MAX} characters.`;
  if (!reason) errors.reason = "Say why it must be kept. The audit log records it.";
  else if (reason.length > REASON_MAX) errors.reason = `At most ${REASON_MAX.toLocaleString("en-US")} characters.`;
  const picker = targetInfo(draft.target_type).picker;
  if (picker === "id") {
    if (!draft.target.trim()) errors.target = "Paste its id or a link to it.";
    else if (!parseTargetId(draft.target)) errors.target = "That has no id in it. Copy the link from the item's page.";
  } else if (!draft.target) {
    errors.target = picker === "person" ? "Choose the person." : "Choose the collection.";
  }
  return errors;
}

/** Call only when holdDraftErrors() is empty. */
export function holdDraftToBody(draft: HoldDraft): HoldIn {
  const picker = targetInfo(draft.target_type).picker;
  return {
    matter: draft.matter.trim(),
    reason: draft.reason.trim(),
    target_type: draft.target_type,
    target_id: picker === "id" ? parseTargetId(draft.target)! : draft.target,
  };
}

function plural(n: number, one: string, many: string): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}

/** "3 documents and 1 meeting", or "nothing yet". */
export function coversSummary(covers: HoldCovers): string {
  const parts = [
    covers.documents ? plural(covers.documents, "document", "documents") : null,
    covers.conversations ? plural(covers.conversations, "conversation", "conversations") : null,
    covers.meetings ? plural(covers.meetings, "meeting", "meetings") : null,
    covers.analytics_runs ? plural(covers.analytics_runs, "Insights question", "Insights questions") : null,
  ].filter((p): p is string => p !== null);
  if (parts.length === 0) return "nothing yet";
  if (parts.length === 1) return parts[0];
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** Active holds grouped by matter, matters in alphabetical order. */
export function byMatter(holds: readonly LegalHold[]): { matter: string; holds: LegalHold[] }[] {
  const groups = new Map<string, LegalHold[]>();
  for (const hold of holds) {
    const key = hold.matter;
    groups.set(key, [...(groups.get(key) ?? []), hold]);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([matter, items]) => ({ matter, holds: items }));
}
