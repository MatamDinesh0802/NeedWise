import { describe, expect, it } from "vitest";

import { ORG_ROLES, type OrgRole } from "@/lib/session";

import {
  ADMIN_SECTIONS,
  adminSectionForPath,
  canAccessSection,
  firstAdminSection,
  visibleAdminSections,
} from "./sections";

const keys = (role: OrgRole | null | undefined) => visibleAdminSections(role).map((s) => s.key);

describe("admin sections by role", () => {
  it.each([
    ["owner", ["people", "departments", "groups", "collections", "sources", "agents", "workflows", "analytics", "budgets", "audit"]],
    ["admin", ["people", "departments", "groups", "collections", "sources", "agents", "workflows", "analytics", "budgets", "audit"]],
    ["auditor", ["audit"]],
    ["member", []],
    ["guest", []],
  ] as const)("%s sees %j", (role, expected) => {
    expect(keys(role)).toEqual(expected);
  });

  it("shows nothing while loading or signed out", () => {
    expect(keys(undefined)).toEqual([]);
    expect(keys(null)).toEqual([]);
  });

  it("sends /admin to the first allowed section", () => {
    expect(firstAdminSection("owner")?.href).toBe("/admin/people");
    expect(firstAdminSection("admin")?.href).toBe("/admin/people");
    expect(firstAdminSection("auditor")?.href).toBe("/admin/audit");
    expect(firstAdminSection("member")).toBeNull();
  });

  it("only auditors-and-up can read the audit log; only owners/admins manage", () => {
    const audit = ADMIN_SECTIONS.find((s) => s.key === "audit")!;
    const people = ADMIN_SECTIONS.find((s) => s.key === "people")!;
    for (const role of ORG_ROLES) {
      expect(canAccessSection(audit, role), role).toBe(["owner", "admin", "auditor"].includes(role));
      expect(canAccessSection(people, role), role).toBe(["owner", "admin"].includes(role));
    }
  });
});

describe("adminSectionForPath", () => {
  it.each([
    ["/admin/people", "people"],
    ["/admin/departments", "departments"],
    ["/admin/departments/0190-abc", "departments"],
    ["/admin/groups/x/y", "groups"],
    ["/admin/audit", "audit"],
    ["/admin/collections", "collections"],
    ["/admin/agents", "agents"],
    ["/admin/analytics", "analytics"],
    ["/admin/budgets", "budgets"],
  ])("%s → %s", (path, key) => {
    expect(adminSectionForPath(path)?.key).toBe(key);
  });

  it.each(["/admin", "/admin/peoplex", "/settings"])("%s → none", (path) => {
    expect(adminSectionForPath(path)).toBeNull();
  });
});
