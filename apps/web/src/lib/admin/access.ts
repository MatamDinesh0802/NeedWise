/**
 * Who may open an admin page, decided in two steps so the page itself can always be rendered:
 *
 * 1. `adminAccess(session)`: from the session alone (no URL). While it is "checking" (on the
 *    server, and in the browser until /api/v1/me answers) the shell keeps the page in the tree
 *    but hidden: Next.js can then validate the page's instant navigation, and nobody sees a page
 *    before we know they may open it.
 * 2. `pageVerdict(access, pathname)`: the section the URL is in, for the person's role.
 *
 * Cosmetic only, like every role check in the web app: the API enforces each permission (403).
 */

import { adminSectionForPath, canAccessSection, visibleAdminSections, type AdminSection } from "@/lib/admin/sections";
import type { Me } from "@/lib/session";
import type { SessionState } from "@/lib/session-store";

export type AdminAccess =
  | { kind: "checking" }
  | { kind: "signed_out" }
  | { kind: "error"; message: string }
  /** Signed in, but no admin section is open to this role. */
  | { kind: "no_admin"; me: Me }
  | { kind: "admin"; me: Me; sections: AdminSection[] };

export function adminAccess(state: SessionState): AdminAccess {
  switch (state.kind) {
    case "loading":
      return { kind: "checking" };
    case "signed_out":
      return { kind: "signed_out" };
    case "error":
      return { kind: "error", message: state.message };
    case "signed_in": {
      const sections = visibleAdminSections(state.me.org_role);
      return sections.length === 0 ? { kind: "no_admin", me: state.me } : { kind: "admin", me: state.me, sections };
    }
  }
}

/** Whether the shell keeps the page in the tree at all (hidden or shown). */
export function keepsPage(access: AdminAccess): access is { kind: "checking" } | Extract<AdminAccess, { kind: "admin" }> {
  return access.kind === "checking" || access.kind === "admin";
}

export type PageVerdict =
  | { kind: "checking" }
  | { kind: "allowed" }
  /** The URL is in a section this role can't open; `fallback` is one it can. */
  | { kind: "denied"; section: AdminSection; fallback: AdminSection };

export function pageVerdict(access: AdminAccess, pathname: string | null): PageVerdict {
  if (access.kind !== "admin") return { kind: "checking" };
  const current = pathname ? adminSectionForPath(pathname) : null;
  if (current && !canAccessSection(current, access.me.org_role)) {
    return { kind: "denied", section: current, fallback: access.sections[0] };
  }
  return { kind: "allowed" };
}
