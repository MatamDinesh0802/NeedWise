/**
 * Admin sub-sections and who sees them. Mirrors the API's rules (owners and admins
 * manage everything; auditors only read the audit log). Cosmetic only: the API
 * enforces every permission on its own.
 */

import type { OrgRole } from "@/lib/session";

export type AdminSectionKey =
  | "people"
  | "departments"
  | "groups"
  | "collections"
  | "governance"
  | "sources"
  | "agents"
  | "workflows"
  | "analytics"
  | "models"
  | "budgets"
  | "audit"
  | "retention"
  | "legal-holds";

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
    key: "governance",
    title: "Governance",
    href: "/admin/governance",
    description: "Document owners, reviews that are overdue or due soon, requests for official status and recent demotions.",
    roles: MANAGERS,
  },
  {
    key: "sources",
    title: "Sources",
    href: "/admin/sources",
    description: "Websites, GitHub, Slack and Google Drive kept in sync with a collection, and its access rules.",
    roles: MANAGERS,
  },
  {
    key: "agents",
    title: "Agents",
    href: "/admin/agents",
    description: "Department agents the assistant can call, the collections they search and when to use them.",
    roles: MANAGERS,
  },
  {
    key: "workflows",
    title: "Workflows",
    href: "/admin/workflows",
    description: "Scheduled and manual automations. Steps that change something wait for approval.",
    roles: MANAGERS,
  },
  {
    key: "analytics",
    title: "Analytics",
    href: "/admin/analytics",
    description: "The data people can ask about in Insights: uploaded tables, and the data model that defines metrics and who sees which rows.",
    roles: MANAGERS,
  },
  {
    key: "models",
    title: "Models",
    href: "/admin/models",
    description: "Your own API keys and model servers, the model catalog with prices, which model does what, and what may leave your infrastructure.",
    roles: MANAGERS,
  },
  {
    key: "budgets",
    title: "Budgets",
    href: "/admin/budgets",
    description: "Spending limits on model usage: for the organization, a default cap per person, caps for some people and, with Enterprise, per department.",
    roles: MANAGERS,
  },
  {
    key: "audit",
    title: "Audit log",
    href: "/admin/audit",
    description: "Every sign-in and change, in a tamper-evident chain: filter it, page through it and export it.",
    roles: ["owner", "admin", "auditor"],
  },
  {
    key: "retention",
    title: "Retention",
    href: "/admin/retention",
    description: "How long conversations, BI runs, meeting recordings and read notifications are kept. Enterprise.",
    roles: MANAGERS,
  },
  {
    key: "legal-holds",
    title: "Legal holds",
    href: "/admin/legal-holds",
    description: "Keep a person's conversations, meetings and Insights questions, a collection, a document or a meeting from being deleted while a legal matter is open. Enterprise.",
    roles: MANAGERS,
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
