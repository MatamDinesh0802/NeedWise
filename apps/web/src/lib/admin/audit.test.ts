import { describe, expect, it } from "vitest";

import type { AuditEvent } from "./api";
import { actorLabel, AUDIT_ACTION_FILTERS, formatDetails, hasDetails, resourceLabel, verifyResultView } from "./audit";

describe("verifyResultView", () => {
  it("reports an intact chain with the number of events checked", () => {
    expect(verifyResultView({ ok: true, checked: 1234, first_broken_seq: null })).toEqual({
      tone: "ok",
      title: "Chain intact",
      description: "Chain intact, 1,234 events checked. No event was changed, removed or reordered.",
    });
  });

  it("uses the singular for one event", () => {
    expect(verifyResultView({ ok: true, checked: 1, first_broken_seq: null }).description).toContain("1 event checked");
  });

  it("handles an empty log", () => {
    expect(verifyResultView({ ok: true, checked: 0, first_broken_seq: null })).toMatchObject({
      tone: "ok",
      description: "There are no audit events yet.",
    });
  });

  it("names the first broken event", () => {
    const view = verifyResultView({ ok: false, checked: 57, first_broken_seq: 42 });
    expect(view.tone).toBe("danger");
    expect(view.title).toBe("Tampering detected at event #42");
    expect(view.description).toContain("57 events checked");
    expect(view.description).toContain("#42");
  });

  it("still reports tampering when the API gives no sequence number", () => {
    expect(verifyResultView({ ok: false, checked: 3, first_broken_seq: null })).toMatchObject({
      tone: "danger",
      title: "Tampering detected",
    });
  });
});

const event = (overrides: Partial<AuditEvent> = {}): AuditEvent => ({
  seq: 7,
  occurred_at: "2026-11-04T05:54:58Z",
  actor_type: "user",
  actor: { id: "u1", display_name: "Erin Takahashi" },
  action: "admin.user.update",
  resource_type: "user",
  resource_id: "u2",
  details: { org_role: ["member", "admin"] },
  ip: "172.20.0.1",
  ...overrides,
});

describe("audit row helpers", () => {
  it("labels actors", () => {
    expect(actorLabel(event())).toBe("Erin Takahashi");
    expect(actorLabel(event({ actor: null, actor_type: "system" }))).toBe("System");
    expect(actorLabel(event({ actor: null, actor_type: "seed" }))).toBe("Seed");
    expect(actorLabel(event({ actor: null, actor_type: "" }))).toBe("Unknown");
  });

  it("labels resources", () => {
    expect(resourceLabel(event())).toBe("user u2");
    expect(resourceLabel(event({ resource_id: null }))).toBe("user");
    expect(resourceLabel(event({ resource_type: null, resource_id: null }))).toBe("—");
  });

  it("knows when there are details and pretty-prints them", () => {
    expect(hasDetails(event())).toBe(true);
    expect(hasDetails(event({ details: {} }))).toBe(false);
    expect(formatDetails({ a: 1 })).toBe('{\n  "a": 1\n}');
  });

  it("offers the action prefixes from the contract", () => {
    expect(AUDIT_ACTION_FILTERS.map((f) => f.value)).toEqual(["", "auth.", "admin.", "knowledge.", "audit.", "seed."]);
  });
});
