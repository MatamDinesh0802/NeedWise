/**
 * Typed client for /api/v1/admin. Shapes mirror the Pydantic models in
 * apps/api/src/needwise/api/v1/admin.py. All calls go through the shared wrapper
 * (CSRF header, problem+json errors).
 */

import { apiJson } from "@/lib/api";
import type { DepartmentRole, OrgRole } from "@/lib/session";

export type UserStatus = "invited" | "active" | "disabled";

export interface MembershipRef {
  id: string;
  slug: string;
  name: string;
  role?: string | null;
}

export interface AdminUser {
  id: string;
  email: string;
  display_name: string;
  title: string | null;
  org_role: OrgRole;
  status: UserStatus;
  last_login_at: string | null;
  departments: MembershipRef[];
  groups: MembershipRef[];
}

export interface UserPage {
  items: AdminUser[];
  next_cursor: string | null;
}

export interface PersonRef {
  id: string;
  display_name: string;
}

export interface Department {
  id: string;
  slug: string;
  name: string;
  member_count: number;
  managers: PersonRef[];
}

export interface DepartmentMember {
  user_id: string;
  email: string;
  display_name: string;
  role: DepartmentRole;
}

export interface Group {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  source: string;
  member_count: number;
}

export interface GroupMember {
  kind: "user" | "group";
  id: string;
  name: string;
  email?: string | null;
}

export interface AuditEvent {
  seq: number;
  occurred_at: string;
  actor_type: string;
  actor: PersonRef | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  details: Record<string, unknown>;
  ip: string | null;
}

export interface AuditPage {
  items: AuditEvent[];
  next_before_seq: number | null;
  /** Opaque: pass it back as `cursor` for the next (older) page. null: no more events. */
  next_cursor: string | null;
}

/** The audit explorer's filters, as the API takes them (api/v1/admin.py AuditFilters). */
export interface AuditQuery {
  action?: string;
  actor_id?: string;
  resource_type?: string;
  resource_id?: string;
  /** ISO 8601 with a time zone; `from` inclusive, `to` exclusive. */
  from?: string;
  to?: string;
  q?: string;
}

export interface ChainResult {
  ok: boolean;
  checked: number;
  first_broken_seq: number | null;
}

interface Items<T> {
  items: T[];
}

const BASE = "/api/v1/admin";

/** Builds a query string, skipping empty values. */
export function queryString(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}

const id = (value: string) => encodeURIComponent(value);

export const adminApi = {
  // People
  listUsers: (params: { q?: string; status?: UserStatus | ""; limit?: number; cursor?: string | null }) =>
    apiJson<UserPage>(`${BASE}/users${queryString(params)}`),
  createUser: (body: { email: string; display_name: string; title?: string | null; org_role: OrgRole }) =>
    apiJson<AdminUser>(`${BASE}/users`, { method: "POST", json: body }),
  updateUser: (
    userId: string,
    body: Partial<{ display_name: string; title: string; org_role: OrgRole; status: "active" | "disabled" }>,
  ) => apiJson<AdminUser>(`${BASE}/users/${id(userId)}`, { method: "PATCH", json: body }),

  // Departments
  listDepartments: () => apiJson<Items<Department>>(`${BASE}/departments`),
  createDepartment: (body: { slug: string; name: string }) =>
    apiJson<Department>(`${BASE}/departments`, { method: "POST", json: body }),
  renameDepartment: (departmentId: string, name: string) =>
    apiJson<Department>(`${BASE}/departments/${id(departmentId)}`, { method: "PATCH", json: { name } }),
  deleteDepartment: (departmentId: string) =>
    apiJson<void>(`${BASE}/departments/${id(departmentId)}`, { method: "DELETE" }),
  listDepartmentMembers: (departmentId: string) =>
    apiJson<Items<DepartmentMember>>(`${BASE}/departments/${id(departmentId)}/members`),
  setDepartmentMember: (departmentId: string, userId: string, role: DepartmentRole) =>
    apiJson<DepartmentMember>(`${BASE}/departments/${id(departmentId)}/members/${id(userId)}`, {
      method: "PUT",
      json: { role },
    }),
  removeDepartmentMember: (departmentId: string, userId: string) =>
    apiJson<void>(`${BASE}/departments/${id(departmentId)}/members/${id(userId)}`, { method: "DELETE" }),

  // Groups
  listGroups: () => apiJson<Items<Group>>(`${BASE}/groups`),
  createGroup: (body: { slug: string; name: string; description?: string | null }) =>
    apiJson<Group>(`${BASE}/groups`, { method: "POST", json: body }),
  deleteGroup: (groupId: string) => apiJson<void>(`${BASE}/groups/${id(groupId)}`, { method: "DELETE" }),
  listGroupMembers: (groupId: string) => apiJson<Items<GroupMember>>(`${BASE}/groups/${id(groupId)}/members`),
  addGroupUser: (groupId: string, userId: string) =>
    apiJson<void>(`${BASE}/groups/${id(groupId)}/members/users/${id(userId)}`, { method: "PUT" }),
  removeGroupUser: (groupId: string, userId: string) =>
    apiJson<void>(`${BASE}/groups/${id(groupId)}/members/users/${id(userId)}`, { method: "DELETE" }),
  addGroupChild: (groupId: string, childId: string) =>
    apiJson<void>(`${BASE}/groups/${id(groupId)}/members/groups/${id(childId)}`, { method: "PUT" }),
  removeGroupChild: (groupId: string, childId: string) =>
    apiJson<void>(`${BASE}/groups/${id(groupId)}/members/groups/${id(childId)}`, { method: "DELETE" }),

  // Audit
  listAuditEvents: (params: AuditQuery & { cursor?: string | null; before_seq?: number | null; limit?: number }) =>
    apiJson<AuditPage>(`${BASE}/audit-events${queryString({ ...params })}`),
  /** Plain links: the browser downloads with the session cookie (Content-Disposition: attachment). */
  auditCsvUrl: (params: AuditQuery) => `${BASE}/audit-events/export.csv${queryString({ ...params })}`,
  /** Enterprise "advanced_audit_export": the whole hash chain as JSON lines. */
  auditChainUrl: (afterSeq?: number | null) => `${BASE}/audit-events/export.jsonl${queryString({ after_seq: afterSeq ?? null })}`,
  verifyAuditChain: () => apiJson<ChainResult>(`${BASE}/audit-events/verify`),
};

/** Query-cache keys. Invalidate by prefix after mutations. */
export const adminKeys = {
  all: "admin/",
  users: "admin/users",
  userSearch: (q: string) => `admin/users/search?q=${q}`,
  departments: "admin/departments",
  departmentMembers: (departmentId: string) => `admin/departments/${departmentId}/members`,
  groups: "admin/groups",
  groupMembers: (groupId: string) => `admin/groups/${groupId}/members`,
};
