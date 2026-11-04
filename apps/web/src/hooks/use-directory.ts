"use client";

import { useMemo } from "react";

import { useApiQuery } from "@/hooks/use-api-query";
import { adminApi, adminKeys, type AdminUser } from "@/lib/admin/api";
import type { Directory } from "@/lib/knowledge/principals";
import type { QueryState } from "@/lib/stores/query-cache";

const MAX_USER_PAGES = 10; // 1,000 people; beyond that names show as "Unknown person"

/** Every person (following the cursor), for turning `user:<id>` into a name. */
export async function fetchAllUsers(): Promise<AdminUser[]> {
  const users: AdminUser[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < MAX_USER_PAGES; page++) {
    const result = await adminApi.listUsers({ limit: 100, cursor });
    users.push(...result.items);
    cursor = result.next_cursor;
    if (!cursor) break;
  }
  return users;
}

export const allUsersKey = `${adminKeys.users}/all`;

/**
 * People, groups and departments (admin endpoints) as lookup maps for principal names.
 * Admin-only: other users get 403s, which callers show as unknown names.
 */
export function useDirectory(tenant: { id: string; name: string }) {
  const users = useApiQuery(allUsersKey, fetchAllUsers);
  const groups = useApiQuery(adminKeys.groups, adminApi.listGroups);
  const departments = useApiQuery(adminKeys.departments, adminApi.listDepartments);

  const directory = useMemo<Directory>(() => {
    const data = <T,>(state: QueryState<T>) => (state.status === "success" ? state.data : null);
    return {
      tenant,
      users: new Map((data(users.state) ?? []).map((u) => [u.id, { display_name: u.display_name, email: u.email }])),
      groups: new Map((data(groups.state)?.items ?? []).map((g) => [g.id, { name: g.name }])),
      departments: new Map((data(departments.state)?.items ?? []).map((d) => [d.id, { name: d.name }])),
    };
  }, [tenant, users.state, groups.state, departments.state]);

  const loading = [users.state, groups.state, departments.state].some((s) => s.status === "loading");
  const error = [users.state, groups.state, departments.state].find((s) => s.status === "error");
  return {
    directory,
    loading,
    error: error?.status === "error" ? error.error : null,
    groups: groups.state.status === "success" ? groups.state.data.items : [],
    departments: departments.state.status === "success" ? departments.state.data.items : [],
  };
}
