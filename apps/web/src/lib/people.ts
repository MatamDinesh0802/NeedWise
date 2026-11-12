/**
 * The colleague directory for members (GET /api/v1/people, api/v1/me.py): assigning tasks,
 * naming approvers and assignees, mapping meeting speakers. Guests get 403. Admin screens keep
 * using /admin/users.
 */

import { apiJson } from "@/lib/api";
import type { OrgRole } from "@/lib/session";

export interface Person {
  id: string;
  display_name: string;
  email: string;
  title: string | null;
  departments: string[];
}

export const PEOPLE_LIMIT = 500;

export const peopleApi = {
  list: (q = "", limit = PEOPLE_LIMIT) =>
    apiJson<Person[]>(`/api/v1/people?limit=${limit}${q.trim() ? `&q=${encodeURIComponent(q.trim())}` : ""}`),
};

export const peopleKeys = { all: "people/all" };

/** Guests never see the directory (the API answers 403); don't ask. */
export function canUseDirectory(role: OrgRole | null | undefined): boolean {
  return role != null && role !== "guest";
}

/** "You" for me, the colleague's name, or a fallback when they aren't in the directory. */
export function personName(
  userId: string | null | undefined,
  { meId, people, fallback = "Someone else" }: { meId: string | null; people: ReadonlyMap<string, Pick<Person, "display_name">>; fallback?: string },
): string {
  if (!userId) return fallback;
  if (userId === meId) return "You";
  return people.get(userId)?.display_name ?? fallback;
}
