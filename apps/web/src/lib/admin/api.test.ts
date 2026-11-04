import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CSRF_HEADER } from "@/lib/api";

import { adminApi, queryString } from "./api";

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async () => new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => vi.unstubAllGlobals());

function lastCall() {
  const [url, init] = fetchMock.mock.calls.at(-1)!;
  const headers = new Headers(init?.headers);
  return { url, method: init?.method, body: init?.body ? JSON.parse(String(init.body)) : undefined, headers };
}

describe("queryString", () => {
  it("skips empty values", () => {
    expect(queryString({ q: "", status: undefined, limit: 25, cursor: null })).toBe("?limit=25");
    expect(queryString({})).toBe("");
    expect(queryString({ q: "a b&c" })).toBe("?q=a+b%26c");
  });
});

describe("adminApi", () => {
  it("lists users with search, status and cursor", async () => {
    await adminApi.listUsers({ q: "ali", status: "active", limit: 25, cursor: "abc" });
    expect(lastCall()).toMatchObject({ url: "/api/v1/admin/users?q=ali&status=active&limit=25&cursor=abc", method: "GET" });
    expect(lastCall().headers.has(CSRF_HEADER)).toBe(false);
  });

  it.each([
    ["createUser", () => adminApi.createUser({ email: "x@acme.test", display_name: "X", org_role: "member" }), "POST", "/api/v1/admin/users"],
    ["updateUser", () => adminApi.updateUser("u 1", { status: "disabled" }), "PATCH", "/api/v1/admin/users/u%201"],
    ["createDepartment", () => adminApi.createDepartment({ slug: "hr", name: "HR" }), "POST", "/api/v1/admin/departments"],
    ["renameDepartment", () => adminApi.renameDepartment("d1", "People"), "PATCH", "/api/v1/admin/departments/d1"],
    ["deleteDepartment", () => adminApi.deleteDepartment("d1"), "DELETE", "/api/v1/admin/departments/d1"],
    ["setDepartmentMember", () => adminApi.setDepartmentMember("d1", "u1", "manager"), "PUT", "/api/v1/admin/departments/d1/members/u1"],
    ["removeDepartmentMember", () => adminApi.removeDepartmentMember("d1", "u1"), "DELETE", "/api/v1/admin/departments/d1/members/u1"],
    ["createGroup", () => adminApi.createGroup({ slug: "g", name: "G" }), "POST", "/api/v1/admin/groups"],
    ["deleteGroup", () => adminApi.deleteGroup("g1"), "DELETE", "/api/v1/admin/groups/g1"],
    ["addGroupUser", () => adminApi.addGroupUser("g1", "u1"), "PUT", "/api/v1/admin/groups/g1/members/users/u1"],
    ["removeGroupUser", () => adminApi.removeGroupUser("g1", "u1"), "DELETE", "/api/v1/admin/groups/g1/members/users/u1"],
    ["addGroupChild", () => adminApi.addGroupChild("g1", "g2"), "PUT", "/api/v1/admin/groups/g1/members/groups/g2"],
    ["removeGroupChild", () => adminApi.removeGroupChild("g1", "g2"), "DELETE", "/api/v1/admin/groups/g1/members/groups/g2"],
  ])("%s sends %s %s with the CSRF header", async (_name, call, method, url) => {
    await call();
    const req = lastCall();
    expect(req.method).toBe(method);
    expect(req.url).toBe(url);
    expect(req.headers.get(CSRF_HEADER)).toBe("1");
  });

  it("sends the right JSON bodies", async () => {
    await adminApi.setDepartmentMember("d1", "u1", "manager");
    expect(lastCall().body).toEqual({ role: "manager" });
    await adminApi.renameDepartment("d1", "People & HR");
    expect(lastCall().body).toEqual({ name: "People & HR" });
    await adminApi.updateUser("u1", { org_role: "admin" });
    expect(lastCall().body).toEqual({ org_role: "admin" });
  });

  it("pages the audit log with an action prefix", async () => {
    await adminApi.listAuditEvents({ action: "auth.", before_seq: 120, limit: 50 });
    expect(lastCall().url).toBe("/api/v1/admin/audit-events?action=auth.&before_seq=120&limit=50");
    await adminApi.listAuditEvents({ action: "", before_seq: null, limit: 50 });
    expect(lastCall().url).toBe("/api/v1/admin/audit-events?limit=50");
    await adminApi.verifyAuditChain();
    expect(lastCall().url).toBe("/api/v1/admin/audit-events/verify");
  });

  it("returns undefined for 204 responses", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(adminApi.deleteGroup("g1")).resolves.toBeUndefined();
  });
});
