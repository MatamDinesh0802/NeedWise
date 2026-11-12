import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { canUseDirectory, peopleApi, peopleKeys, peoplePath, personDetail, personName } from "./people";

describe("the colleague directory", () => {
  it("is for members, never for guests (the API answers them 403) or before the session loads", () => {
    expect(canUseDirectory("member")).toBe(true);
    expect(canUseDirectory("auditor")).toBe(true);
    expect(canUseDirectory("owner")).toBe(true);
    expect(canUseDirectory("guest")).toBe(false);
    expect(canUseDirectory(undefined)).toBe(false);
    expect(canUseDirectory(null)).toBe(false);
  });

  it("asks for everyone (up to the API's 500) or searches by name or email", () => {
    expect(peoplePath()).toBe("/api/v1/people?limit=500");
    expect(peoplePath("  Wei Zhang ", 8)).toBe("/api/v1/people?limit=8&q=Wei+Zhang");
    expect(peopleKeys.search(" Wei ")).toBe(peopleKeys.search("wei"));
  });

  it("names a person: 'You', the colleague, or the fallback", () => {
    const people = new Map([["u-wei", { display_name: "Wei Zhang" }]]);
    expect(personName("u-me", { meId: "u-me", people })).toBe("You");
    expect(personName("u-wei", { meId: "u-me", people })).toBe("Wei Zhang");
    expect(personName("u-gone", { meId: "u-me", people })).toBe("Someone else");
    expect(personName("u-gone", { meId: "u-me", people, fallback: "the approver" })).toBe("the approver");
    expect(personName(null, { meId: "u-me", people, fallback: "Nobody" })).toBe("Nobody");
  });

  it("describes a person under their name", () => {
    expect(personDetail({ title: "Fleet engineer", departments: ["Engineering", "Operations"] })).toBe("Fleet engineer · Engineering, Operations");
    expect(personDetail({ title: null, departments: [] })).toBe("");
  });
});

describe("peopleApi", () => {
  const fetchMock = vi.fn<typeof fetch>();
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockImplementation(async () => new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("GETs /api/v1/people", async () => {
    await expect(peopleApi.list("zhang", 8)).resolves.toEqual([]);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/people?limit=8&q=zhang");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("GET");
  });
});
