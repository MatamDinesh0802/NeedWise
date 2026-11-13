import { describe, expect, it } from "vitest";

import {
  byMatter,
  coversSummary,
  emptyHoldDraft,
  HOLD_TARGETS,
  holdDraftErrors,
  holdDraftToBody,
  parseTargetId,
  type LegalHold,
} from "./legal-holds";

const ID = "0190f5a4-0000-7000-8000-000000000001";

describe("what a hold can name", () => {
  it("matches the API's targets, and says what each keeps", () => {
    expect(HOLD_TARGETS.map((t) => t.type).sort()).toEqual(["analytics_run", "collection", "document", "meeting", "user"]);
    for (const target of HOLD_TARGETS) expect(target.keeps.length).toBeGreaterThan(10);
  });

  it("finds the id in an id or a link to the item", () => {
    expect(parseTargetId(ID)).toBe(ID);
    expect(parseTargetId(`https://needwise.acme.test/knowledge/documents/${ID.toUpperCase()}`)).toBe(ID);
    expect(parseTargetId(`/insights?run=${ID}`)).toBe(ID);
    expect(parseTargetId("the travel policy")).toBeNull();
  });
});

describe("the hold form", () => {
  it("needs a matter, a reason and what to hold", () => {
    expect(holdDraftErrors(emptyHoldDraft("user"))).toEqual({
      matter: "Name the matter, e.g. “Smith v. Acme”.",
      reason: "Say why it must be kept. The audit log records it.",
      target: "Choose the person.",
    });
    expect(holdDraftErrors({ ...emptyHoldDraft("collection"), matter: "M", reason: "R" }).target).toBe("Choose the collection.");
    expect(holdDraftErrors({ ...emptyHoldDraft("document"), matter: "M", reason: "R", target: "travel" }).target).toContain("no id in it");
    expect(holdDraftErrors({ ...emptyHoldDraft("document"), matter: "x".repeat(201), reason: "R", target: ID }).matter).toBe("At most 200 characters.");
  });

  it("sends the trimmed matter and reason, and the id found in a link", () => {
    const draft = { matter: " Smith v. Acme ", reason: " Discovery ", target_type: "meeting" as const, target: `/meetings/${ID}` };
    expect(holdDraftErrors(draft)).toEqual({});
    expect(holdDraftToBody(draft)).toEqual({ matter: "Smith v. Acme", reason: "Discovery", target_type: "meeting", target_id: ID });
    expect(holdDraftToBody({ ...draft, target_type: "user", target: "u-bob" }).target_id).toBe("u-bob");
  });
});

describe("summaries", () => {
  it("says what a hold keeps now", () => {
    expect(coversSummary({ documents: 0, conversations: 0, meetings: 0, analytics_runs: 0 })).toBe("nothing yet");
    expect(coversSummary({ documents: 1, conversations: 0, meetings: 0, analytics_runs: 0 })).toBe("1 document");
    expect(coversSummary({ documents: 2, conversations: 14, meetings: 1, analytics_runs: 3 })).toBe(
      "2 documents, 14 conversations, 1 meeting and 3 Insights questions",
    );
  });

  it("groups holds by matter", () => {
    const hold = (id: string, matter: string) => ({ id, matter }) as LegalHold;
    expect(byMatter([hold("1", "Smith v. Acme"), hold("2", "Audit 2026"), hold("3", "Smith v. Acme")]).map((g) => [g.matter, g.holds.map((h) => h.id)])).toEqual([
      ["Audit 2026", ["2"]],
      ["Smith v. Acme", ["1", "3"]],
    ]);
  });
});
