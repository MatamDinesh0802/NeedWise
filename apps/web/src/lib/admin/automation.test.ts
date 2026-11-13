import { describe, expect, it } from "vitest";

import {
  describeDraft,
  draftProblems,
  draftToRule,
  rowsChanged,
  rowsFrom,
  ruleToDraft,
  rulesToSave,
  splitTerms,
  type RuleDraft,
} from "./automation";

describe("approver rule builder", () => {
  it("reads every rule the API stores and writes it back the same", () => {
    const rules: unknown[] = [
      "olivia.grant@acme.test",
      { role: "org_admins" },
      { department_manager: "requester" },
      { department_manager: "finance" },
      { any_of: ["hannah.schmidt@acme.test", { role: "org_admins" }] },
      { chain: [{ department_manager: "requester" }, { any_of: ["a@acme.test", "b@acme.test"] }, "c@acme.test"] },
    ];
    for (const rule of rules) {
      const draft = ruleToDraft(rule);
      expect(draft, JSON.stringify(rule)).not.toBeNull();
      expect(draftToRule(draft as RuleDraft)).toEqual(rule);
    }
  });

  it("has no rule by default and leaves out empty steps", () => {
    expect(ruleToDraft(undefined)).toEqual([]);
    expect(draftToRule([])).toBeNull();
    expect(draftToRule([[], [{ kind: "org_admins", value: "" }]])).toEqual({ role: "org_admins" });
  });

  it("refuses what it can't show, so the page offers the raw rule instead", () => {
    expect(ruleToDraft({ role: "everyone" })).toBeNull();
    expect(ruleToDraft({ chain: [] })).toBeNull();
    expect(ruleToDraft({ chain: [{ chain: ["a@acme.test"] }] })).toBeNull();
    expect(ruleToDraft(42)).toBeNull();
  });

  it("says what is missing and reads the rule in words", () => {
    const draft: RuleDraft = [
      [{ kind: "requester_manager", value: "" }],
      [
        { kind: "department_manager", value: "" },
        { kind: "user", value: "not an email" },
      ],
    ];
    expect(draftProblems(draft)).toEqual(["Step 2: choose a department.", "Step 2: choose a colleague."]);
    expect(draftProblems([[]])).toEqual(["add at least one approver, or remove the step."]);
    const complete: RuleDraft = [
      [{ kind: "requester_manager", value: "" }],
      [
        { kind: "department_manager", value: "finance" },
        { kind: "user", value: "priya.raman@acme.test" },
      ],
    ];
    expect(draftProblems(complete)).toEqual([]);
    expect(describeDraft(complete, (key) => (key === "finance" ? "Finance" : key))).toBe(
      "a manager of the requester's department, then a manager of Finance or priya.raman@acme.test",
    );
    expect(describeDraft([])).toMatch(/^The person who asked/);
  });
});

describe("routing rules", () => {
  it("splits keywords on commas, semicolons and lines, once each", () => {
    expect(splitTerms("invoice, Refund;  expense   report\nrefund,,")).toEqual(["invoice", "Refund", "expense report"]);
  });

  it("round-trips saved rules and checks every row before saving", () => {
    const rows = rowsFrom([
      { id: "r1", department: "finance", department_name: "Finance", keywords: ["invoice", "refund"], categories: ["Billing"], enabled: true },
    ]);
    expect(rows[0]).toMatchObject({ keywords: "invoice, refund", categories: "Billing" });
    const saved = [{ id: "r1", department: "finance", department_name: "Finance", keywords: ["invoice", "refund"], categories: ["Billing"], enabled: true }];
    expect(rowsChanged(rows, saved)).toBe(false);
    expect(rowsChanged([{ ...rows[0], key: "other-key" }], saved)).toBe(false);
    expect(rowsChanged([{ ...rows[0], enabled: false }], saved)).toBe(true);
    expect(rulesToSave(rows)).toEqual({
      ok: true,
      rules: [{ department: "finance", keywords: ["invoice", "refund"], categories: ["Billing"], enabled: true }],
    });
    const bad = rulesToSave([...rows, { key: "n1", department: "", keywords: "x", categories: "", enabled: true }, { key: "n2", department: "hr", keywords: " ", categories: "", enabled: true }]);
    expect(bad).toEqual({ ok: false, errors: { 1: "Choose a department.", 2: "Add a keyword or a category." } });
  });
});
