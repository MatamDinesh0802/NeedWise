import { describe, expect, it } from "vitest";

import type { AdminUser } from "./api";
import { assignableRoles, rowActions, toggledStatus } from "./people";

const user = (overrides: Partial<AdminUser> = {}): AdminUser => ({
  id: "u2",
  email: "bob@acme.test",
  display_name: "Bob",
  title: null,
  org_role: "member",
  status: "active",
  last_login_at: null,
  departments: [],
  groups: [],
  ...overrides,
});

describe("assignableRoles", () => {
  it("hides the owner role unless the current user is an owner", () => {
    expect(assignableRoles("owner")).toContain("owner");
    expect(assignableRoles("admin")).not.toContain("owner");
    expect(assignableRoles("admin")).toEqual(["admin", "auditor", "member", "guest"]);
  });
});

describe("rowActions", () => {
  const admin = { id: "u1", org_role: "admin" as const };
  const owner = { id: "u0", org_role: "owner" as const };

  it("disables actions on your own row", () => {
    expect(rowActions(admin, user({ id: "u1", org_role: "admin" }))).toEqual({
      canChangeRole: false,
      canToggleStatus: false,
      reason: expect.stringMatching(/your own/),
    });
  });

  it("lets only owners act on owners", () => {
    expect(rowActions(admin, user({ org_role: "owner" })).canChangeRole).toBe(false);
    expect(rowActions(owner, user({ org_role: "owner" })).canChangeRole).toBe(true);
  });

  it("allows admins to manage everyone else", () => {
    expect(rowActions(admin, user())).toEqual({ canChangeRole: true, canToggleStatus: true, reason: null });
  });
});

describe("toggledStatus", () => {
  it.each([
    ["active", "disabled"],
    ["invited", "disabled"],
    ["disabled", "active"],
  ] as const)("%s → %s", (from, to) => {
    expect(toggledStatus(from)).toBe(to);
  });
});
