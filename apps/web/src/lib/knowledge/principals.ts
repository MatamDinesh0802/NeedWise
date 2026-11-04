/**
 * Collection sharing: principal strings ↔ readable names, and the PUT/POST payload.
 * Principals: user:<uuid> · group:<uuid> · dept:<uuid> (members) ·
 * dept-manager:<uuid> (managers) · org:<tenant uuid> (everyone except guests).
 */

import type { CollectionScope, Grant } from "@/lib/knowledge/api";

export type PrincipalKind = "user" | "group" | "dept" | "dept-manager" | "org";

export interface ParsedPrincipal {
  kind: PrincipalKind;
  id: string;
}

const PRINCIPAL = /^(user|group|dept|dept-manager|org):([0-9a-f-]{36})$/;

export function parsePrincipal(principal: string): ParsedPrincipal | null {
  const match = PRINCIPAL.exec(principal);
  return match ? { kind: match[1] as PrincipalKind, id: match[2] } : null;
}

export function principal(kind: PrincipalKind, id: string): string {
  return `${kind}:${id}`;
}

export interface Directory {
  tenant: { id: string; name: string };
  users: ReadonlyMap<string, { display_name: string; email: string }>;
  groups: ReadonlyMap<string, { name: string }>;
  departments: ReadonlyMap<string, { name: string }>;
}

export interface PrincipalLabel {
  label: string;
  /** "Person", "Group", "Department", … */
  kindLabel: string;
  detail: string | null;
  /** False if the id isn't in the directory (deleted, or beyond what we loaded). */
  known: boolean;
}

const short = (id: string) => id.slice(0, 8);

export function principalLabel(value: string, directory: Directory): PrincipalLabel {
  const parsed = parsePrincipal(value);
  if (!parsed) return { label: value, kindLabel: "Unknown", detail: null, known: false };
  const { kind, id } = parsed;
  switch (kind) {
    case "user": {
      const user = directory.users.get(id);
      return user
        ? { label: user.display_name, kindLabel: "Person", detail: user.email, known: true }
        : { label: `Unknown person (${short(id)})`, kindLabel: "Person", detail: null, known: false };
    }
    case "group": {
      const group = directory.groups.get(id);
      return group
        ? { label: group.name, kindLabel: "Group", detail: null, known: true }
        : { label: `Unknown group (${short(id)})`, kindLabel: "Group", detail: null, known: false };
    }
    case "dept":
    case "dept-manager": {
      const dept = directory.departments.get(id);
      const who = kind === "dept" ? "members" : "managers";
      return dept
        ? { label: `${dept.name} ${who}`, kindLabel: "Department", detail: null, known: true }
        : { label: `Unknown department ${who} (${short(id)})`, kindLabel: "Department", detail: null, known: false };
    }
    case "org":
      return id === directory.tenant.id
        ? {
            label: `Everyone at ${directory.tenant.name}`,
            kindLabel: "Organization",
            detail: "except guests",
            known: true,
          }
        : { label: `Another organization (${short(id)})`, kindLabel: "Organization", detail: null, known: false };
  }
}

/** Sort for display: everyone, departments, groups, then people; alphabetical within. */
const KIND_ORDER: Record<PrincipalKind, number> = { org: 0, dept: 1, "dept-manager": 2, group: 3, user: 4 };

export function sortPrincipals(values: readonly string[], directory: Directory): string[] {
  return [...values].sort((a, b) => {
    const ka = KIND_ORDER[parsePrincipal(a)?.kind ?? "user"];
    const kb = KIND_ORDER[parsePrincipal(b)?.kind ?? "user"];
    return ka - kb || principalLabel(a, directory).label.localeCompare(principalLabel(b, directory).label);
  });
}

// --- Sharing model -------------------------------------------------------------------------

export type AccessLevel = "view" | "edit";

/** One row in the sharing editor. Editors can always view (OpenFGA: editor ⇒ viewer). */
export interface ShareEntry {
  principal: string;
  level: AccessLevel;
}

/** API grants → one entry per principal, at the highest level granted. */
export function grantsToEntries(grants: readonly Grant[]): ShareEntry[] {
  const levels = new Map<string, AccessLevel>();
  for (const grant of grants) {
    if (grant.relation === "editor") levels.set(grant.principal, "edit");
    else if (grant.relation === "viewer" && !levels.has(grant.principal)) levels.set(grant.principal, "view");
  }
  return [...levels].map(([p, level]) => ({ principal: p, level }));
}

/**
 * Entries → `{viewers, editors}` for POST/PUT: view-level entries are viewers, edit-level
 * entries are editors (editors can view anyway). This round-trips: saving unchanged sharing
 * sends back exactly the grants the API returned. Deduplicated, sorted, invalid dropped.
 */
export function buildSharingPayload(entries: readonly ShareEntry[]): { viewers: string[]; editors: string[] } {
  const viewers = new Set<string>();
  const editors = new Set<string>();
  for (const entry of entries) {
    if (!parsePrincipal(entry.principal)) continue;
    (entry.level === "edit" ? editors : viewers).add(entry.principal);
  }
  for (const editor of editors) viewers.delete(editor);
  return { viewers: [...viewers].sort(), editors: [...editors].sort() };
}

/** Adds or updates one principal (the higher level wins when re-adding). */
export function upsertEntry(entries: readonly ShareEntry[], next: ShareEntry): ShareEntry[] {
  const existing = entries.find((e) => e.principal === next.principal);
  if (!existing) return [...entries, next];
  return entries.map((e) => (e.principal === next.principal ? { ...e, level: next.level } : e));
}

/** The viewer the API adds automatically when creating a collection with this scope. */
export function scopeDefaultViewer(
  scope: Exclude<CollectionScope, "personal">,
  tenantId: string,
  departmentId: string | null,
): string | null {
  if (scope === "enterprise") return principal("org", tenantId);
  if (scope === "department" && departmentId) return principal("dept", departmentId);
  return null;
}

export const SCOPE_DESCRIPTIONS: Record<CollectionScope, string> = {
  enterprise: "Everyone in the organization except guests.",
  department: "That department's members.",
  restricted: "Only the people and groups you pick.",
  personal: "Only its owner.",
};

/** Problems that would make the API reject the sharing (or that are surely mistakes). */
export function sharingProblems({
  scope,
  departmentId,
  entries,
  creating,
}: {
  scope: CollectionScope;
  departmentId: string | null;
  entries: readonly ShareEntry[];
  creating: boolean;
}): string[] {
  const problems: string[] = [];
  if (creating && scope === "department" && !departmentId) problems.push("Choose the department.");
  if (scope === "restricted" && entries.length === 0) {
    problems.push("A restricted collection needs at least one person or group who can view it.");
  }
  return problems;
}
