import { describe, expect, it } from "vitest";

import {
  buildSharingPayload,
  grantsToEntries,
  parsePrincipal,
  principalLabel,
  scopeDefaultViewer,
  sharingProblems,
  sortPrincipals,
  upsertEntry,
  type Directory,
} from "./principals";

const T = "00000000-0000-7000-8000-0000000000aa";
const ALICE = "00000000-0000-7000-8000-000000000001";
const BOB = "00000000-0000-7000-8000-000000000002";
const GONE = "00000000-0000-7000-8000-0000000000ff";
const HR_RECORDS = "00000000-0000-7000-8000-0000000000b1";
const PEOPLE = "00000000-0000-7000-8000-0000000000c1";

const directory: Directory = {
  tenant: { id: T, name: "Acme Robotics" },
  users: new Map([
    [ALICE, { display_name: "Alice Moreau", email: "alice@acme.test" }],
    [BOB, { display_name: "Bob Okafor", email: "bob@acme.test" }],
  ]),
  groups: new Map([[HR_RECORDS, { name: "HR records" }]]),
  departments: new Map([[PEOPLE, { name: "People & HR" }]]),
};

describe("parsePrincipal", () => {
  it("parses every kind and rejects junk", () => {
    expect(parsePrincipal(`user:${ALICE}`)).toEqual({ kind: "user", id: ALICE });
    expect(parsePrincipal(`dept-manager:${PEOPLE}`)).toEqual({ kind: "dept-manager", id: PEOPLE });
    expect(parsePrincipal(`org:${T}`)).toEqual({ kind: "org", id: T });
    expect(parsePrincipal("user:alice")).toBeNull();
    expect(parsePrincipal(`admin:${ALICE}`)).toBeNull();
    expect(parsePrincipal("")).toBeNull();
  });
});

describe("principalLabel", () => {
  it.each([
    [`user:${ALICE}`, "Alice Moreau", "Person", "alice@acme.test"],
    [`group:${HR_RECORDS}`, "HR records", "Group", null],
    [`dept:${PEOPLE}`, "People & HR members", "Department", null],
    [`dept-manager:${PEOPLE}`, "People & HR managers", "Department", null],
    [`org:${T}`, "Everyone at Acme Robotics", "Organization", "except guests"],
  ])("%s → %s", (value, label, kindLabel, detail) => {
    expect(principalLabel(value, directory)).toEqual({ label, kindLabel, detail, known: true });
  });

  it("flags ids it can't resolve instead of hiding them", () => {
    expect(principalLabel(`user:${GONE}`, directory)).toMatchObject({ label: "Unknown person (00000000)", known: false });
    expect(principalLabel(`group:${GONE}`, directory).known).toBe(false);
    expect(principalLabel(`dept:${GONE}`, directory).label).toMatch(/Unknown department members/);
    expect(principalLabel(`org:${GONE}`, directory)).toMatchObject({ kindLabel: "Organization", known: false });
    expect(principalLabel("garbage", directory)).toMatchObject({ label: "garbage", known: false });
  });

  it("sorts everyone, departments, groups, then people by name", () => {
    expect(
      sortPrincipals([`user:${BOB}`, `group:${HR_RECORDS}`, `user:${ALICE}`, `org:${T}`, `dept-manager:${PEOPLE}`, `dept:${PEOPLE}`], directory),
    ).toEqual([`org:${T}`, `dept:${PEOPLE}`, `dept-manager:${PEOPLE}`, `group:${HR_RECORDS}`, `user:${ALICE}`, `user:${BOB}`]);
  });
});

describe("sharing payload", () => {
  it("collapses grants to one entry per principal; editor wins", () => {
    expect(
      grantsToEntries([
        { principal: `dept:${PEOPLE}`, relation: "viewer" },
        { principal: `user:${ALICE}`, relation: "viewer" },
        { principal: `user:${ALICE}`, relation: "editor" },
        { principal: `group:${HR_RECORDS}`, relation: "owner" }, // unknown relation ignored
      ]),
    ).toEqual([
      { principal: `dept:${PEOPLE}`, level: "view" },
      { principal: `user:${ALICE}`, level: "edit" },
    ]);
  });

  it("splits entries into viewers and editors, deduplicated, sorted, junk dropped", () => {
    expect(
      buildSharingPayload([
        { principal: `user:${BOB}`, level: "view" },
        { principal: `user:${ALICE}`, level: "edit" },
        { principal: `group:${HR_RECORDS}`, level: "view" },
        { principal: `user:${BOB}`, level: "view" },
        { principal: "not-a-principal", level: "edit" },
      ]),
    ).toEqual({
      viewers: [`group:${HR_RECORDS}`, `user:${BOB}`].sort(),
      editors: [`user:${ALICE}`],
    });
  });

  it("round-trips grants → entries → payload, so saving unchanged sharing changes nothing", () => {
    const grants = [
      { principal: `org:${T}`, relation: "viewer" },
      { principal: `group:${HR_RECORDS}`, relation: "editor" },
      { principal: `dept-manager:${PEOPLE}`, relation: "editor" },
    ];
    expect(buildSharingPayload(grantsToEntries(grants))).toEqual({
      viewers: [`org:${T}`],
      editors: [`dept-manager:${PEOPLE}`, `group:${HR_RECORDS}`].sort(),
    });
  });

  it("a principal granted both ways is only sent as an editor", () => {
    const grants = [
      { principal: `user:${ALICE}`, relation: "viewer" },
      { principal: `user:${ALICE}`, relation: "editor" },
    ];
    expect(buildSharingPayload(grantsToEntries(grants))).toEqual({ viewers: [], editors: [`user:${ALICE}`] });
  });

  it("an editors-only restricted collection is sent as-is (the API accepts it)", () => {
    expect(buildSharingPayload([{ principal: `group:${HR_RECORDS}`, level: "edit" }])).toEqual({
      viewers: [],
      editors: [`group:${HR_RECORDS}`],
    });
  });

  it("an empty editor list is still a valid payload", () => {
    expect(buildSharingPayload([])).toEqual({ viewers: [], editors: [] });
  });

  it("upserts entries without duplicates", () => {
    const entries = [{ principal: `user:${ALICE}`, level: "view" as const }];
    expect(upsertEntry(entries, { principal: `user:${ALICE}`, level: "edit" })).toEqual([{ principal: `user:${ALICE}`, level: "edit" }]);
    expect(upsertEntry(entries, { principal: `user:${BOB}`, level: "view" })).toHaveLength(2);
  });
});

describe("scope rules", () => {
  it("knows the viewer the API adds for each scope", () => {
    expect(scopeDefaultViewer("enterprise", T, null)).toBe(`org:${T}`);
    expect(scopeDefaultViewer("department", T, PEOPLE)).toBe(`dept:${PEOPLE}`);
    expect(scopeDefaultViewer("department", T, null)).toBeNull();
    expect(scopeDefaultViewer("restricted", T, PEOPLE)).toBeNull();
  });

  it("requires viewers for restricted and a department for department scope", () => {
    expect(sharingProblems({ scope: "restricted", departmentId: null, entries: [], creating: true })).toHaveLength(1);
    expect(sharingProblems({ scope: "restricted", departmentId: null, entries: [{ principal: `user:${ALICE}`, level: "edit" }], creating: true })).toEqual([]);
    expect(sharingProblems({ scope: "department", departmentId: null, entries: [], creating: true })).toEqual(["Choose the department."]);
    expect(sharingProblems({ scope: "department", departmentId: null, entries: [], creating: false })).toEqual([]);
    expect(sharingProblems({ scope: "enterprise", departmentId: null, entries: [], creating: true })).toEqual([]);
  });
});
