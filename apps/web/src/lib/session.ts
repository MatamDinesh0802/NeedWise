/**
 * Types and pure helpers for the signed-in user (GET /api/v1/me).
 * Framework-free so they can be unit-tested in isolation.
 */

import { AUTH_ERROR_PARAM } from "@/lib/auth-errors";

export const ORG_ROLES = ["owner", "admin", "auditor", "member", "guest"] as const;
export type OrgRole = (typeof ORG_ROLES)[number];

export type DepartmentRole = "manager" | "member";

export interface Me {
  id: string;
  email: string;
  display_name: string;
  title: string | null;
  org_role: OrgRole;
  tenant: { id: string; slug: string; name: string };
  departments: { id: string; slug: string; name: string; role: DepartmentRole }[];
  groups: { id: string; slug: string; name: string }[];
}

export const ORG_ROLE_LABELS: Record<OrgRole, string> = {
  owner: "Owner",
  admin: "Admin",
  auditor: "Auditor",
  member: "Member",
  guest: "Guest",
};

export const DEPARTMENT_ROLE_LABELS: Record<DepartmentRole, string> = {
  manager: "Manager",
  member: "Member",
};

/**
 * Org roles that see the Administration section in the UI.
 * Cosmetic only: the API enforces every permission on its own.
 */
export const ADMIN_VIEW_ROLES: readonly OrgRole[] = ["owner", "admin", "auditor"];

export function canSeeAdministration(role: OrgRole | null | undefined): boolean {
  return role != null && ADMIN_VIEW_ROLES.includes(role);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireString(record: Record<string, unknown>, key: string, where: string): string {
  const value = record[key];
  if (typeof value !== "string") throw new Error(`${where}.${key} must be a string`);
  return value;
}

function parseRef(raw: unknown, where: string) {
  if (!isRecord(raw)) throw new Error(`${where} must be an object`);
  return {
    id: requireString(raw, "id", where),
    slug: requireString(raw, "slug", where),
    name: requireString(raw, "name", where),
  };
}

/** Validates the /me payload. Throws if it does not match the contract. */
export function parseMe(payload: unknown): Me {
  if (!isRecord(payload)) throw new Error("me must be an object");
  const role = payload.org_role;
  if (typeof role !== "string" || !(ORG_ROLES as readonly string[]).includes(role)) {
    throw new Error("me.org_role is invalid");
  }
  if (!Array.isArray(payload.departments) || !Array.isArray(payload.groups)) {
    throw new Error("me.departments and me.groups must be arrays");
  }
  if (payload.title !== null && typeof payload.title !== "string") {
    throw new Error("me.title must be a string or null");
  }

  return {
    id: requireString(payload, "id", "me"),
    email: requireString(payload, "email", "me"),
    display_name: requireString(payload, "display_name", "me"),
    title: payload.title,
    org_role: role as OrgRole,
    tenant: parseRef(payload.tenant, "me.tenant"),
    departments: payload.departments.map((raw, i) => {
      const ref = parseRef(raw, `me.departments[${i}]`);
      const deptRole = (raw as Record<string, unknown>).role;
      if (deptRole !== "manager" && deptRole !== "member") {
        throw new Error(`me.departments[${i}].role is invalid`);
      }
      return { ...ref, role: deptRole };
    }),
    groups: payload.groups.map((raw, i) => parseRef(raw, `me.groups[${i}]`)),
  };
}

/** Up to two initials from the display name, falling back to the email. */
export function initials(displayName: string, email: string): string {
  const words = displayName.trim().split(/\s+/).filter(Boolean);
  const first = (word: string) => Array.from(word)[0] ?? "";
  if (words.length >= 2) return (first(words[0]) + first(words[words.length - 1])).toUpperCase();
  if (words.length === 1) return first(words[0]).toUpperCase();
  return (Array.from(email.split("@")[0] ?? "").slice(0, 2).join("") || "?").toUpperCase();
}

/**
 * The path to return to after sign-in: current path and query, without the
 * auth error flag. Only same-origin relative paths are allowed.
 */
export function returnToFor(pathname: string, search = ""): string {
  if (!pathname.startsWith("/") || pathname.startsWith("//") || pathname.includes("\\")) return "/";
  const params = new URLSearchParams(search);
  params.delete(AUTH_ERROR_PARAM);
  const query = params.toString();
  return query ? `${pathname}?${query}` : pathname;
}

/** Sign-in is a plain navigation (not fetch): the API redirects to the identity provider. */
export function loginHref(pathname: string, search = ""): string {
  return `/api/auth/login?return_to=${encodeURIComponent(returnToFor(pathname, search))}`;
}
