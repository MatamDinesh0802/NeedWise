import { describe, expect, it } from "vitest";

import { alice } from "@/test/fixtures";

import { canSeeAdministration, initials, loginHref, parseMe, returnToFor } from "./session";

describe("parseMe", () => {
  it("accepts the /me contract", () => {
    expect(parseMe(alice)).toEqual(alice);
  });

  it("accepts a null title and empty memberships", () => {
    expect(parseMe({ ...alice, title: null, departments: [], groups: [] }).title).toBeNull();
  });

  it.each([
    ["non-object", null],
    ["unknown org role", { ...alice, org_role: "superuser" }],
    ["missing tenant", { ...alice, tenant: undefined }],
    ["bad department role", { ...alice, departments: [{ id: "d", slug: "s", name: "n", role: "boss" }] }],
    ["groups not an array", { ...alice, groups: null }],
    ["numeric title", { ...alice, title: 3 }],
  ])("rejects %s", (_label, payload) => {
    expect(() => parseMe(payload)).toThrow();
  });
});

describe("canSeeAdministration", () => {
  it.each([
    ["owner", true],
    ["admin", true],
    ["auditor", true],
    ["member", false],
    ["guest", false],
  ] as const)("%s → %s", (role, expected) => {
    expect(canSeeAdministration(role)).toBe(expected);
  });

  it("is false without a role", () => {
    expect(canSeeAdministration(undefined)).toBe(false);
    expect(canSeeAdministration(null)).toBe(false);
  });
});

describe("initials", () => {
  it.each([
    ["Alice Anders", "alice@acme.test", "AA"],
    ["Erin van der Berg", "erin@acme.test", "EB"],
    ["Mallory", "mallory@acme.test", "M"],
    ["  ", "dan@acme.test", "DA"],
    ["", "", "?"],
    ["Émile Zola", "e@x.test", "ÉZ"],
  ])("%j / %s → %s", (name, email, expected) => {
    expect(initials(name, email)).toBe(expected);
  });
});

describe("returnToFor / loginHref", () => {
  it("keeps the path and query but drops auth_error", () => {
    expect(returnToFor("/knowledge", "?tab=docs&auth_error=login_failed")).toBe("/knowledge?tab=docs");
    expect(returnToFor("/", "?auth_error=x")).toBe("/");
  });

  it.each(["https://evil.example/", "//evil.example/", "/\\evil.example", "javascript:alert(1)"])(
    "refuses to return to %s",
    (path) => {
      expect(returnToFor(path)).toBe("/");
    },
  );

  it("builds an encoded login URL", () => {
    expect(loginHref("/agents", "?q=a b")).toBe("/api/auth/login?return_to=%2Fagents%3Fq%3Da%2Bb");
    expect(loginHref("/")).toBe("/api/auth/login?return_to=%2F");
  });
});
