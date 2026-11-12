/**
 * Knowledge governance (docs/governance.md): document owners, review cycles, the "official"
 * approval flow, and the overview of what needs attention. View logic is framework-free and
 * unit-tested; the API decides who may do what, these helpers only explain it.
 */

import { apiJson } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { KnowledgeDocument, OfficialRequest, PersonRef, Trust } from "@/lib/knowledge/api";

const DAY_MS = 24 * 3600 * 1000;
/** "Due soon" in the overview and on documents (modules/knowledge/governance.py DUE_SOON). */
export const DUE_SOON_DAYS = 30;
export const NOTE_MAX = 2000;

// --- Review state ------------------------------------------------------------------------------

export type ReviewState = "none" | "overdue" | "due_soon" | "ok";

type ReviewFields = Pick<KnowledgeDocument, "reviewed_at" | "reviewed_by" | "next_review_at" | "review_overdue">;

/** none: the collection has no review cycle. */
export function reviewState(doc: Pick<ReviewFields, "next_review_at" | "review_overdue">, now: number = Date.now()): ReviewState {
  if (!doc.next_review_at) return "none";
  const next = new Date(doc.next_review_at).getTime();
  if (Number.isNaN(next)) return "none";
  if (doc.review_overdue || next < now) return "overdue";
  return next - now < DUE_SOON_DAYS * DAY_MS ? "due_soon" : "ok";
}

/** Whole days until (positive) or since (negative) the next review. */
export function daysUntil(iso: string, now: number = Date.now()): number {
  return Math.round((new Date(iso).getTime() - now) / DAY_MS);
}

function days(n: number): string {
  return `${n} ${n === 1 ? "day" : "days"}`;
}

/** A short label for lists and badges, or null when nothing is due. */
export function reviewBadge(doc: Pick<ReviewFields, "next_review_at" | "review_overdue">, now: number = Date.now()): string | null {
  const state = reviewState(doc, now);
  if (state === "overdue") {
    const late = -daysUntil(doc.next_review_at!, now);
    return late >= 1 ? `Review overdue by ${days(late)}` : "Review overdue";
  }
  if (state === "due_soon") {
    const left = daysUntil(doc.next_review_at!, now);
    return left <= 0 ? "Review due today" : `Review due in ${days(left)}`;
  }
  return null;
}

/** "You" for the signed-in person, else the name. */
export function personLabel(person: PersonRef | null | undefined, meId: string | null | undefined, fallback = "someone"): string {
  if (!person) return fallback;
  return person.id === meId ? "you" : person.display_name;
}

/** "Reviewed 3 Mar 2026 by Priya Raman; next review 3 Mar 2027." */
export function reviewSummary(doc: ReviewFields, meId?: string | null): string {
  const reviewed = doc.reviewed_at
    ? `Reviewed ${formatDate(doc.reviewed_at)}${doc.reviewed_by ? ` by ${personLabel(doc.reviewed_by, meId)}` : ""}`
    : "Not reviewed yet";
  if (!doc.next_review_at) return `${reviewed}. This collection has no review cycle.`;
  const next = formatDate(doc.next_review_at);
  return doc.reviewed_at ? `${reviewed}; next review ${next}.` : `${reviewed}; first review due ${next}.`;
}

// --- Official status ---------------------------------------------------------------------------

/** "Priya Raman", "Erin or Amara", "Erin, Amara or Sam". */
export function joinOr(names: readonly string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;
}

/** "Requested by you on 12 Nov 2026. Waiting for Priya Raman." */
export function officialRequestSummary(request: OfficialRequest, meId?: string | null): string {
  const who = personLabel(request.requested_by, meId, "someone");
  const waiting = request.approvers.map((a) => (a.id === meId ? "you" : a.display_name));
  const tail = waiting.length ? ` Waiting for ${joinOr(waiting)}.` : "";
  return `Requested by ${who} on ${formatDate(request.requested_at)}.${tail}`;
}

/** Why the request button is or isn't offered, in words (null: it can be requested). */
export function officialBlocker(doc: Pick<KnowledgeDocument, "trust" | "official_request">, canEdit: boolean): string | null {
  if (doc.trust === "official") return "This document is already official.";
  if (doc.official_request) return "Official status has been requested; the approver decides.";
  if (!canEdit) return "Only the collection's editors can ask for official status.";
  return null;
}

export function noteError(note: string, { required }: { required: boolean }): string | null {
  const text = note.trim();
  if (required && !text) return "Say why it should be official. The approver reads this.";
  if (text.length > NOTE_MAX) return `Keep it under ${NOTE_MAX.toLocaleString("en-US")} characters.`;
  return null;
}

export const DEMOTE_TO: readonly { value: Exclude<Trust, "official">; label: string }[] = [
  { value: "reference", label: "Reference" },
  { value: "discussion", label: "Discussion" },
];

// --- Overview (GET /governance/overview) -------------------------------------------------------

export interface GovernanceCollection {
  id: string;
  name: string;
  /** False: the caller manages it but cannot open it, so document titles are withheld. */
  can_view: boolean;
  owner_id: string | null;
  review_interval_days: number | null;
}

export interface GovernedDocument {
  id: string;
  /** null when the caller cannot open the collection (separation of duties). */
  title: string | null;
  collection_id: string;
  trust: Trust;
  owner: PersonRef | null;
  /** The owner left (disabled): someone else should take it over. */
  owner_inactive: boolean;
  reviewed_at: string | null;
  reviewed_by: PersonRef | null;
  next_review_at: string | null;
}

export interface PendingOfficial {
  document: GovernedDocument;
  requested_by: PersonRef | null;
  requested_at: string;
  note: string | null;
  approvers: PersonRef[];
}

export interface Demotion {
  document_id: string;
  title: string | null;
  collection_id: string;
  by: PersonRef | null;
  at: string;
  to: string;
  note: string | null;
}

export interface GovernanceOverview {
  /** all: an org owner or admin; owned: the collections the caller owns. */
  scope: "all" | "owned";
  collections: GovernanceCollection[];
  overdue: GovernedDocument[];
  due_soon: GovernedDocument[];
  without_owner: GovernedDocument[];
  pending_official: PendingOfficial[];
  recent_demotions: Demotion[];
}

export const governanceApi = {
  overview: () => apiJson<GovernanceOverview>("/api/v1/governance/overview"),
};

/** Under "knowledge/", so any knowledge change (reviews, owners, trust) refreshes it. */
export const governanceKeys = {
  overview: "knowledge/governance/overview",
};

export function documentHref(documentId: string): string {
  return `/knowledge/documents/${encodeURIComponent(documentId)}`;
}

/** The title, or what to say when it's withheld. */
export function documentLabel(doc: Pick<GovernedDocument, "title">, collection: GovernanceCollection | undefined): string {
  if (doc.title) return doc.title;
  return `A document in ${collection?.name ?? "a collection"} you can't open`;
}

export interface OverviewCounts {
  overdue: number;
  due_soon: number;
  without_owner: number;
  pending_official: number;
  recent_demotions: number;
}

export function overviewCounts(overview: GovernanceOverview): OverviewCounts {
  return {
    overdue: overview.overdue.length,
    due_soon: overview.due_soon.length,
    without_owner: overview.without_owner.length,
    pending_official: overview.pending_official.length,
    recent_demotions: overview.recent_demotions.length,
  };
}

/** Nothing needs attention. */
export function overviewIsClear(overview: GovernanceOverview): boolean {
  return Object.values(overviewCounts(overview)).every((n) => n === 0);
}

export function ownerText(doc: Pick<GovernedDocument, "owner" | "owner_inactive">): string {
  if (!doc.owner) return "No owner";
  return doc.owner_inactive ? `${doc.owner.display_name} (left)` : doc.owner.display_name;
}
