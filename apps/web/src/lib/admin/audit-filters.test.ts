import { describe, expect, it } from "vitest";

import { adminApi, type AuditEvent } from "./api";
import {
  activeFilterCount,
  auditFilterErrors,
  auditQuery,
  EMPTY_AUDIT_FILTERS,
  filtersFromSearch,
  filtersToSearch,
  localMidnightIso,
  withActor,
  withResource,
  type AuditFilterState,
} from "./audit";

const erin = { id: "u-erin", display_name: "Erin Takahashi" };
const filters = (overrides: Partial<AuditFilterState> = {}): AuditFilterState => ({ ...EMPTY_AUDIT_FILTERS, ...overrides });

describe("audit explorer filters", () => {
  it("sends nothing for an empty form", () => {
    expect(auditQuery(EMPTY_AUDIT_FILTERS)).toEqual({});
    expect(activeFilterCount(EMPTY_AUDIT_FILTERS)).toBe(0);
  });

  it("maps every field to the API's names, trimmed, with a whole end day", () => {
    const query = auditQuery(
      filters({ action: " knowledge. ", actor: erin, resourceType: "document", resourceId: "d-1", from: "2026-11-01", to: "2026-11-12", q: " travel " }),
    );
    expect(query).toEqual({
      action: "knowledge.",
      actor_id: "u-erin",
      resource_type: "document",
      resource_id: "d-1",
      from: new Date(2026, 10, 1).toISOString(),
      to: new Date(2026, 10, 13).toISOString(), // midnight after the 12th: the 12th is included
      q: "travel",
    });
  });

  it("checks the dates", () => {
    expect(localMidnightIso("2026-13-45")).not.toBeNull(); // Date rolls over; the input type=date never sends this
    expect(localMidnightIso("yesterday")).toBeNull();
    expect(auditFilterErrors(filters({ from: "2026-11-12", to: "2026-11-01" }))).toEqual({ to: "The end date is before the start date." });
    expect(auditFilterErrors(filters({ from: "2026-11-01", to: "2026-11-01" }))).toEqual({});
    expect(auditFilterErrors(filters({ from: "soon" }))).toEqual({ from: "Enter a date." });
  });

  it("counts the filters that narrow the view", () => {
    expect(activeFilterCount(filters({ action: "auth.", actor: erin, q: "x" }))).toBe(3);
  });

  it("round-trips through the page URL, resolving the actor's name when known", () => {
    const state = filters({ action: "admin.", actor: erin, resourceType: "collection", from: "2026-11-01", q: "board" });
    const search = filtersToSearch(state);
    expect(search).toBe("action=admin.&actor_id=u-erin&resource_type=collection&from=2026-11-01&q=board");
    expect(filtersFromSearch(new URLSearchParams(search), new Map([["u-erin", "Erin Takahashi"]]))).toEqual(state);
    expect(filtersFromSearch(new URLSearchParams("actor_id=u-x&from=not-a-date")).actor).toEqual({ id: "u-x", display_name: "Someone" });
    expect(filtersFromSearch(new URLSearchParams("from=not-a-date")).from).toBe("");
  });

  it("narrows to one event's actor or resource", () => {
    const event: AuditEvent = {
      seq: 9,
      occurred_at: "2026-11-04T05:54:58Z",
      actor_type: "user",
      actor: erin,
      action: "knowledge.document.demoted",
      resource_type: "document",
      resource_id: "d-1",
      details: {},
      ip: null,
    };
    expect(withActor(EMPTY_AUDIT_FILTERS, event).actor).toEqual(erin);
    expect(withResource(EMPTY_AUDIT_FILTERS, event)).toMatchObject({ resourceType: "document", resourceId: "d-1" });
    expect(withActor(EMPTY_AUDIT_FILTERS, { ...event, actor: null })).toBe(EMPTY_AUDIT_FILTERS);
  });
});

describe("export links", () => {
  it("export the filtered view as CSV, and the whole chain as JSON lines", () => {
    expect(adminApi.auditCsvUrl({ action: "auth.", q: "a&b" })).toBe("/api/v1/admin/audit-events/export.csv?action=auth.&q=a%26b");
    expect(adminApi.auditCsvUrl({})).toBe("/api/v1/admin/audit-events/export.csv");
    expect(adminApi.auditChainUrl()).toBe("/api/v1/admin/audit-events/export.jsonl");
    expect(adminApi.auditChainUrl(120)).toBe("/api/v1/admin/audit-events/export.jsonl?after_seq=120");
  });
});
