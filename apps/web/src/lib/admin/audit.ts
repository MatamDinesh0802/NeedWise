/** View logic for the audit log. Framework-free and unit-tested. */

import type { AuditEvent, ChainResult } from "@/lib/admin/api";

export const AUDIT_ACTION_FILTERS = [
  { value: "", label: "All" },
  { value: "auth.", label: "Sign-in" },
  { value: "admin.", label: "Admin" },
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
