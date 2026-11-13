/**
 * Identity mappings (docs/permission-sync.md): who a principal named by a source (a Google
 * group, a domain, a Slack or GitHub account) is in NeedWise. People are matched by e-mail on
 * their own; anything else grants nothing until an administrator maps it (fail closed).
 *
 *   GET    /api/v1/admin/identity-mappings          mappings, and what is still unmapped
 *   PUT    /api/v1/admin/identity-mappings          map one (applied at once)
 *   DELETE /api/v1/admin/identity-mappings/{id}     remove one (access ends at once)
 */

import { apiJson } from "@/lib/api";

export interface IdentityMapping {
  id: string;
  external: string;
  principal: string;
  /** A person's, group's or department's name; null if it no longer exists. */
  principal_name: string | null;
  created_by: string | null;
  updated_at: string;
}

export interface UnmappedPrincipal {
  external: string;
  /** Documents whose own access list names it: nobody gets access through it. */
  documents: number;
  /** Keys of the sources those documents come from. */
  sources: string[];
}

export interface IdentityMappings {
  mappings: IdentityMapping[];
  unmapped: UnmappedPrincipal[];
}

export interface MappingSaved {
  mapping: IdentityMapping;
  documents_changed: number;
}

const BASE = "/api/v1/admin/identity-mappings";

export const identityMappingsApi = {
  list: () => apiJson<IdentityMappings>(BASE),
  save: (external: string, principal: string) =>
    apiJson<MappingSaved>(BASE, { method: "PUT", json: { external, principal } }),
  remove: (mappingId: string) => apiJson<void>(`${BASE}/${encodeURIComponent(mappingId)}`, { method: "DELETE" }),
};

export const identityMappingsKeys = { all: "admin/identity-mappings" };

const KINDS: Record<string, string> = {
  email: "E-mail address",
  group: "Group in the source",
  domain: "Everyone in a domain",
  "slack-user": "Slack member",
  "github-user": "GitHub account",
  "github-team": "GitHub team",
  "github-org": "GitHub organization",
  anyone: "Anyone with the link",
};

/** "github-team:acme/platform" -> what kind of principal it is, and its value. */
export function describeExternal(external: string): { kind: string; value: string } {
  if (external === "anyone") return { kind: KINDS.anyone, value: "anyone" };
  const at = external.indexOf(":");
  const prefix = at < 0 ? external : external.slice(0, at);
  return { kind: KINDS[prefix] ?? "Source principal", value: at < 0 ? external : external.slice(at + 1) };
}

/** The same check the API makes, so the form can say so before sending. */
export const EXTERNAL_PATTERN = /^(anyone|(email|group|domain|slack-user|github-user|github-team|github-org):[^\s:]{1,255})$/;

export function normalizeExternal(value: string): string {
  return value.trim().toLowerCase();
}

export function isExternal(value: string): boolean {
  return EXTERNAL_PATTERN.test(normalizeExternal(value));
}
