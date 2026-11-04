/**
 * Admin sub-sections and who sees them. Mirrors the API's rules (owners and admins
 * manage everything; auditors only read the audit log). Cosmetic only: the API
 * enforces every permission on its own.
 */

import type { OrgRole } from "@/lib/session";

export type AdminSectionKey = "people" | "departments" | "groups" | "collections" | "audit";

export interface AdminSection {
  key: AdminSectionKey;
  title: string;
  href: `/admin/${string}`;
  description: string;
  roles: readonly OrgRole[];
}

const MANAGERS: readonly OrgRole[] = ["owner", "admin"];

export const ADMIN_SECTIONS: readonly AdminSection[] = [
  {
    key: "people",
    title: "People",
    href: "/admin/people",
    description: "Invite people, change their organization role, and disable accounts.",
    roles: MANAGERS,
  },
  {
    key: "departments",
    title: "Departments",
    href: "/admin/departments",
    description: "Departments, their managers and members.",
    roles: MANAGERS,
  },
  {
    key: "groups",
    title: "Groups",
    href: "/admin/groups",
    description: "Groups of people and nested groups, used for sharing knowledge.",
    roles: MANAGERS,
  },
  {
    key: "collections",
    title: "Collections",
    href: "/admin/collections",
    description: "Who can view and edit each knowledge collection. Managing sharing doesn't let you read restricted documents.",
    roles: MANAGERS,
  },
  {
    key: "audit",
    title: "Audit log",
    href: "/admin/audit",
    description: "Every sign-in and administrative change, in a tamper-evident chain.",
    roles: ["owner", "admin", "auditor"],
  },
];

export function canAccessSection(section: AdminSection, role: OrgRole | null | undefined): boolean {
  return role != null && section.roles.includes(role);
}

export function visibleAdminSections(role: OrgRole | null | undefined): AdminSection[] {
  return ADMIN_SECTIONS.filter((section) => canAccessSection(section, role));
}

/** Where `/admin` should send this user, or null if they have no admin access. */
export function firstAdminSection(role: OrgRole | null | undefined): AdminSection | null {
  return visibleAdminSections(role)[0] ?? null;
}

/** The section that contains `pathname` (e.g. /admin/departments/123 → departments). */
export function adminSectionForPath(pathname: string): AdminSection | null {
  return (
    ADMIN_SECTIONS.find(
      (section) => pathname === section.href || pathname.startsWith(`${section.href}/`),
    ) ?? null
  );
}
