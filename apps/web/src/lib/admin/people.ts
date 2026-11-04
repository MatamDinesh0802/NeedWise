/**
 * UI hints for the People screen. The API enforces the same rules
 * (owner_required, self_change, last_owner); these only avoid dead-end clicks.
 */

import type { AdminUser, UserStatus } from "@/lib/admin/api";
import { ORG_ROLE_LABELS, ORG_ROLES, type OrgRole } from "@/lib/session";

/** Roles the current user may grant. Only owners may grant (or see) "owner". */
export function assignableRoles(currentRole: OrgRole): OrgRole[] {
  return ORG_ROLES.filter((role) => role !== "owner" || currentRole === "owner");
}

export function roleOptions(currentRole: OrgRole) {
  return assignableRoles(currentRole).map((role) => ({ value: role, label: ORG_ROLE_LABELS[role] }));
}

export interface RowActions {
  canChangeRole: boolean;
  canToggleStatus: boolean;
  /** Why actions are unavailable, for a tooltip. */
  reason: string | null;
}

export function rowActions(me: { id: string; org_role: OrgRole }, user: AdminUser): RowActions {
  if (user.id === me.id) {
    return { canChangeRole: false, canToggleStatus: false, reason: "You can't change your own role or status." };
  }
  if (user.org_role === "owner" && me.org_role !== "owner") {
    return { canChangeRole: false, canToggleStatus: false, reason: "Only an owner can change an owner." };
  }
  return { canChangeRole: true, canToggleStatus: true, reason: null };
}

/** The status a "Disable"/"Enable" action sets. */
export function toggledStatus(status: UserStatus): "active" | "disabled" {
  return status === "disabled" ? "active" : "disabled";
}

export const STATUS_FILTERS = [
  { value: "", label: "All statuses" },
  { value: "active", label: "Active" },
  { value: "invited", label: "Invited" },
  { value: "disabled", label: "Disabled" },
] as const;

export type StatusFilter = (typeof STATUS_FILTERS)[number]["value"];
