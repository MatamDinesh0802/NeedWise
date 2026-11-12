/** View logic for the audit log. Framework-free and unit-tested. */

import type { AuditEvent, AuditQuery, ChainResult, PersonRef } from "@/lib/admin/api";

export const AUDIT_ACTION_FILTERS = [
  { value: "", label: "All" },
  { value: "auth.", label: "Sign-in" },
  { value: "admin.", label: "Admin" },
  { value: "knowledge.", label: "Knowledge" },
  { value: "audit.", label: "Exports" },
  { value: "seed.", label: "Seed" },
] as const;

export type AuditActionFilter = (typeof AUDIT_ACTION_FILTERS)[number]["value"];

export interface VerifyView {
  tone: "ok" | "danger";
  title: string;
  description: string;
}

/** What to show after "Verify integrity". */
export function verifyResultView(result: ChainResult): VerifyView {
  const events = `${result.checked.toLocaleString("en-US")} ${result.checked === 1 ? "event" : "events"}`;
  if (result.ok) {
    return {
      tone: "ok",
      title: "Chain intact",
      description:
        result.checked === 0
          ? "There are no audit events yet."
          : `Chain intact, ${events} checked. No event was changed, removed or reordered.`,
    };
  }
  const at = result.first_broken_seq;
  return {
    tone: "danger",
    title: at == null ? "Tampering detected" : `Tampering detected at event #${at}`,
    description:
      at == null
        ? `The chain is broken (${events} checked). Investigate the audit table.`
        : `Event #${at} or an earlier one was changed, removed or reordered (${events} checked). Investigate from event #${at}.`,
  };
}

export function actorLabel(event: AuditEvent): string {
  if (event.actor) return event.actor.display_name;
  if (event.actor_type === "system") return "System";
  return event.actor_type ? event.actor_type.charAt(0).toUpperCase() + event.actor_type.slice(1) : "Unknown";
}

export function resourceLabel(event: AuditEvent): string {
  if (!event.resource_type) return "—";
  return event.resource_id ? `${event.resource_type} ${event.resource_id}` : event.resource_type;
}

export function hasDetails(event: AuditEvent): boolean {
  return Object.keys(event.details ?? {}).length > 0;
}

export function formatDetails(details: Record<string, unknown>): string {
  return JSON.stringify(details, null, 2);
}

// --- Explorer filters --------------------------------------------------------------------------

/** What the filter form holds. Dates are yyyy-mm-dd in the viewer's time zone; `to` is inclusive. */
export interface AuditFilterState {
  action: string;
  actor: PersonRef | null;
  resourceType: string;
  resourceId: string;
  from: string;
  to: string;
  q: string;
}

export const EMPTY_AUDIT_FILTERS: AuditFilterState = {
  action: "",
  actor: null,
  resourceType: "",
  resourceId: "",
  from: "",
  to: "",
  q: "",
};

/** The CSV export's cap (api/v1/admin.py AUDIT_EXPORT_LIMIT). */
export const AUDIT_EXPORT_LIMIT = 10_000;

const DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Local midnight of a yyyy-mm-dd date, `addDays` later, as ISO (UTC). null when not a date. */
export function localMidnightIso(date: string, addDays = 0): string | null {
  const m = DATE.exec(date);
  if (!m) return null;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]) + addDays);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

export function auditFilterErrors(f: AuditFilterState): { from?: string; to?: string } {
  const errors: { from?: string; to?: string } = {};
  if (f.from && !localMidnightIso(f.from)) errors.from = "Enter a date.";
  if (f.to && !localMidnightIso(f.to)) errors.to = "Enter a date.";
  if (!errors.from && !errors.to && f.from && f.to && f.from > f.to) errors.to = "The end date is before the start date.";
  return errors;
}

/** The API's query: `to` covers the whole end day. Empty fields are left out. */
export function auditQuery(f: AuditFilterState): AuditQuery {
  const query: AuditQuery = {};
  if (f.action.trim()) query.action = f.action.trim();
  if (f.actor) query.actor_id = f.actor.id;
  if (f.resourceType.trim()) query.resource_type = f.resourceType.trim();
  if (f.resourceId.trim()) query.resource_id = f.resourceId.trim();
  const from = f.from ? localMidnightIso(f.from) : null;
  const to = f.to ? localMidnightIso(f.to, 1) : null;
  if (from) query.from = from;
  if (to) query.to = to;
  if (f.q.trim()) query.q = f.q.trim();
  return query;
}

/** How many filters narrow the view (the quick action buttons count too). */
export function activeFilterCount(f: AuditFilterState): number {
  return [f.action.trim(), f.actor, f.resourceType.trim(), f.resourceId.trim(), f.from, f.to, f.q.trim()].filter(Boolean).length;
}

/** The page's own URL: shareable, and what links from other pages (e.g. Governance) set. */
export function filtersToSearch(f: AuditFilterState): string {
  const params = new URLSearchParams();
  if (f.action.trim()) params.set("action", f.action.trim());
  if (f.actor) params.set("actor_id", f.actor.id);
  if (f.resourceType.trim()) params.set("resource_type", f.resourceType.trim());
  if (f.resourceId.trim()) params.set("resource_id", f.resourceId.trim());
  if (f.from) params.set("from", f.from);
  if (f.to) params.set("to", f.to);
  if (f.q.trim()) params.set("q", f.q.trim());
  return params.toString();
}

/** From the page URL. The actor's name isn't in it: `names` resolves it when known. */
export function filtersFromSearch(params: URLSearchParams, names: ReadonlyMap<string, string> = new Map()): AuditFilterState {
  const actorId = params.get("actor_id");
  const date = (key: string) => {
    const value = params.get(key) ?? "";
    return DATE.test(value) ? value : "";
  };
  return {
    action: params.get("action") ?? "",
    actor: actorId ? { id: actorId, display_name: names.get(actorId) ?? "Someone" } : null,
    resourceType: params.get("resource_type") ?? "",
    resourceId: params.get("resource_id") ?? "",
    from: date("from"),
    to: date("to"),
    q: params.get("q") ?? "",
  };
}

/** Narrow the view to one event's actor or resource. */
export function withActor(f: AuditFilterState, event: AuditEvent): AuditFilterState {
  return event.actor ? { ...f, actor: event.actor } : f;
}

export function withResource(f: AuditFilterState, event: AuditEvent): AuditFilterState {
  if (!event.resource_type) return f;
  return { ...f, resourceType: event.resource_type, resourceId: event.resource_id ?? "" };
}
