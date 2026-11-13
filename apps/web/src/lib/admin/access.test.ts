import { describe, expect, it } from "vitest";

import type { Me, OrgRole } from "@/lib/session";
import type { SessionState } from "@/lib/session-store";

import { adminAccess, keepsPage, pageVerdict } from "./access";

const me = (org_role: OrgRole): Me =>
  ({
    id: "u1",
    email: "erin@acme.test",
    display_name: "Erin",
    org_role,
    tenant: { id: "t1", slug: "acme", name: "Acme Robotics" },
  }) as unknown as Me;

const signedIn = (role: OrgRole): SessionState => ({ kind: "signed_in", me: me(role), signingOut: false, signOutError: null });

describe("adminAccess (from the session, no URL)", () => {
  it("is still checking while the session loads, and keeps the page in the tree", () => {
    const access = adminAccess({ kind: "loading" });
    expect(access).toEqual({ kind: "checking" });
    expect(keepsPage(access)).toBe(true);
  });

  it("drops the page for people who are signed out, without admin roles, or when /me failed", () => {
    for (const state of [{ kind: "signed_out" }, { kind: "error", message: "down" }, signedIn("member"), signedIn("guest")] as SessionState[]) {
      expect(keepsPage(adminAccess(state))).toBe(false);
    }
    expect(adminAccess(signedIn("member")).kind).toBe("no_admin");
  });

  it("lists the sections an admin or auditor may open", () => {
    const admin = adminAccess(signedIn("admin"));
    const auditor = adminAccess(signedIn("auditor"));
    expect(admin.kind === "admin" && admin.sections.length).toBe(15); // models, budgets, governance, automation, retention and legal holds too
    expect(auditor.kind === "admin" && auditor.sections.map((s) => s.key)).toEqual(["audit"]);
  });
});

describe("pageVerdict (the section the URL is in)", () => {
  it("hides the page until the session is known", () => {
    expect(pageVerdict(adminAccess({ kind: "loading" }), "/admin/people")).toEqual({ kind: "checking" });
    expect(pageVerdict(adminAccess({ kind: "loading" }), null)).toEqual({ kind: "checking" });
  });

  it("shows the page to someone whose role includes the section", () => {
    expect(pageVerdict(adminAccess(signedIn("admin")), "/admin/groups/123")).toEqual({ kind: "allowed" });
    expect(pageVerdict(adminAccess(signedIn("auditor")), "/admin/audit")).toEqual({ kind: "allowed" });
    expect(pageVerdict(adminAccess(signedIn("auditor")), "/admin")).toEqual({ kind: "allowed" }); // the index
  });

  it("denies a section the role doesn't include, pointing at one it does", () => {
    const verdict = pageVerdict(adminAccess(signedIn("auditor")), "/admin/people");
    expect(verdict.kind).toBe("denied");
    if (verdict.kind === "denied") {
      expect(verdict.section.key).toBe("people");
      expect(verdict.fallback.key).toBe("audit");
    }
  });
});
