import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import type { Approval } from "@/lib/work/api";

import {
  addAddresses,
  argumentRows,
  canDecide,
  decisionRequest,
  displayStatus,
  draftFor,
  editPayload,
  initialCardState,
  latestApproval,
  outcomeText,
  reduceCard,
  serverFieldErrors,
  splitAddresses,
  withAssigneeName,
  type CardAction,
  type CardState,
  type EmailDraft,
} from "./approvals";

const NOW = Date.parse("2026-11-04T10:00:00Z");
const OLIVIA = "u-olivia";

function approval(overrides: Partial<Approval> = {}): Approval {
  return {
    id: "ap1",
    tool: "send_email",
    summary: "Send an email to leadership@acme.test: Weekly Operations Report",
    arguments: {
      to: ["leadership@acme.test"],
      cc: [],
      subject: "Weekly Operations Report",
      body: "Safety: no incidents.\nShipping: lead times 2.1 days.",
    },
    edited_arguments: null,
    status: "pending",
    requested_by: OLIVIA,
    approver_id: OLIVIA,
    decided_at: null,
    decision_note: null,
    expires_at: "2026-11-07T10:00:00Z",
    result: null,
    origin: { conversation_id: "c1", run_id: "r1" },
    created_at: "2026-11-04T09:59:00Z",
    ...overrides,
  };
}

const play = (actions: CardAction[], from: CardState = initialCardState) => actions.reduce(reduceCard, from);

describe("status", () => {
  it("shows a pending approval past its deadline as expired, and 'approved' as running", () => {
    expect(displayStatus(approval(), NOW)).toBe("pending");
    expect(displayStatus(approval({ expires_at: "2026-11-04T09:00:00Z" }), NOW)).toBe("expired");
    expect(displayStatus(approval({ status: "approved" }), NOW)).toBe("running");
    expect(displayStatus(approval({ status: "executed" }), NOW)).toBe("executed");
  });

  it("lets only the named approver decide, and only while pending", () => {
    expect(canDecide(approval(), OLIVIA, NOW)).toBe(true);
    expect(canDecide(approval(), "u-erin", NOW)).toBe(false);
    expect(canDecide(approval({ status: "rejected" }), OLIVIA, NOW)).toBe(false);
    expect(canDecide(approval({ expires_at: "2026-11-01T00:00:00Z" }), OLIVIA, NOW)).toBe(false);
    expect(canDecide(approval(), null, NOW)).toBe(false);
  });

  it("explains each outcome", () => {
    expect(outcomeText(approval({ status: "executed", result: { content: "Sent to leadership@acme.test." } }), NOW)).toBe(
      "Sent to leadership@acme.test.",
    );
    expect(outcomeText(approval({ status: "failed", result: { error: "upstream_unavailable: SMTP down" } }), NOW)).toBe(
      "It didn't run: upstream_unavailable: SMTP down",
    );
    expect(outcomeText(approval({ status: "rejected", decision_note: "Wrong week" }), NOW)).toBe("Rejected: “Wrong week”. Nothing ran.");
    expect(outcomeText(approval({ status: "expired" }), NOW)).toBe("No decision in time. Nothing ran.");
    expect(outcomeText(approval(), NOW)).toBeNull();
  });
});

describe("argument rows", () => {
  it("reads an email as To, Subject and Body (Cc only when set)", () => {
    expect(argumentRows("send_email", approval().arguments)).toEqual([
      { label: "To", value: ["leadership@acme.test"] },
      { label: "Subject", value: "Weekly Operations Report" },
      { label: "Body", value: "Safety: no incidents.\nShipping: lead times 2.1 days.", multiline: true },
    ]);
    expect(argumentRows("send_email", { to: ["a@x.test"], cc: ["b@x.test"], subject: "s", body: "b" })?.map((r) => r.label)).toEqual([
      "To",
      "Cc",
      "Subject",
      "Body",
    ]);
  });

  it("reads a task's fields, and falls back to JSON for other tools", () => {
    expect(argumentRows("create_task", { title: "Restock", assignee: null, department: "operations" })).toEqual([
      { label: "Title", value: "Restock" },
      { label: "Assignee", value: "Nobody yet" },
      { label: "Team queue", value: "operations" },
    ]);
    expect(argumentRows("erp.update_stock", { sku: "A1" })).toBeNull();
  });

  it("names a proposed task's assignee from the colleague directory", () => {
    const rows = argumentRows("create_task", { title: "Restock", assignee: "Wei.Zhang@acme.test" })!;
    const people = [{ email: "wei.zhang@acme.test", display_name: "Wei Zhang" }];
    expect(withAssigneeName(rows, people)).toContainEqual({ label: "Assignee", value: "Wei Zhang (Wei.Zhang@acme.test)" });
    expect(withAssigneeName(rows, [])).toEqual(rows); // not in the directory (or a guest): the address as proposed
    const nobody = argumentRows("create_task", { title: "Restock" })!;
    expect(withAssigneeName(nobody, people)).toEqual(nobody);
  });
});

describe("edit payload", () => {
  const email = draftFor("send_email", approval().arguments) as EmailDraft;

  it("sends the full edited email, including an address still being typed", () => {
    const draft: EmailDraft = { ...email, ccInput: "ops@acme.test", body: `${email.body}\n\nBest,\nOlivia` };
    expect(editPayload(draft)).toEqual({
      ok: true,
      arguments: {
        to: ["leadership@acme.test"],
        cc: ["ops@acme.test"],
        subject: "Weekly Operations Report",
        body: "Safety: no incidents.\nShipping: lead times 2.1 days.\n\nBest,\nOlivia",
      },
    });
  });

  it("checks recipients, subject and body before sending", () => {
    expect(editPayload({ ...email, to: [], subject: " ", body: "" })).toEqual({
      ok: false,
      errors: { to: "Add at least one recipient.", subject: "Add a subject.", body: "The email needs a body." },
    });
    expect(editPayload({ ...email, toInput: "bob" })).toEqual({ ok: false, errors: { to: "“bob” isn't an email address." } });
  });

  it("builds a task with empty optional fields as null", () => {
    const task = draftFor("create_task", { title: " Restock AMR parts ", description: "", assignee: "", department: "operations" });
    expect(editPayload(task)).toEqual({
      ok: true,
      arguments: { title: "Restock AMR parts", description: "", assignee: null, department: "operations", due: null },
    });
  });

  it("parses JSON for other tools and insists on an object", () => {
    expect(editPayload({ kind: "json", text: '{"sku": "A1", "qty": 3}' })).toEqual({ ok: true, arguments: { sku: "A1", qty: 3 } });
    expect(editPayload({ kind: "json", text: "[1, 2]" })).toEqual({ ok: false, errors: { json: "The arguments must be a JSON object ({ … })." } });
    const broken = editPayload({ kind: "json", text: "{sku: A1}" });
    expect(broken.ok).toBe(false);
    expect(!broken.ok && broken.errors.json).toMatch(/^Not valid JSON/);
  });

  it("splits and de-duplicates addresses", () => {
    expect(splitAddresses("a@x.test, b@x.test; c@x.test\nd@x.test")).toEqual(["a@x.test", "b@x.test", "c@x.test", "d@x.test"]);
    expect(addAddresses(["A@x.test"], "a@x.test b@x.test")).toEqual(["A@x.test", "b@x.test"]);
  });
});

describe("card state machine", () => {
  const pending = approval();

  it("pending → approve → executed", () => {
    const submitting = play([{ type: "submit", kind: "approve" }]);
    expect(submitting.submitting).toBe("approve");
    expect(decisionRequest(submitting, "approve")).toEqual({ ok: true, body: { decision: "approve" } });

    const executed = approval({ status: "executed", decided_at: "2026-11-04T10:01:00Z", result: { content: "Sent to leadership@acme.test." } });
    const done = reduceCard(submitting, { type: "decided", approval: executed });
    expect(done).toEqual({ ...initialCardState, decided: executed });
    expect(latestApproval(pending, done.decided)).toBe(executed); // the cache may still say pending
  });

  it("pending → approve → failed (the action ran into an error)", () => {
    const failed = approval({ status: "failed", result: { error: "not_configured: Email is not configured (SMTP_HOST)" } });
    const state = play([{ type: "submit", kind: "approve" }, { type: "decided", approval: failed }]);
    expect(latestApproval(pending, state.decided).status).toBe("failed");
  });

  it("pending → reject with a note → rejected", () => {
    let state = play([{ type: "reject" }, { type: "note", note: "  Not this week  " }]);
    expect(state.mode).toBe("reject");
    expect(decisionRequest(state, "reject")).toEqual({ ok: true, body: { decision: "reject", note: "Not this week" } });
    expect(decisionRequest({ ...state, note: "" }, "reject")).toEqual({ ok: true, body: { decision: "reject" } });
    state = play([{ type: "submit", kind: "reject" }, { type: "decided", approval: approval({ status: "rejected", decision_note: "Not this week" }) }], state);
    expect(state.mode).toBe("view");
    expect(state.decided?.status).toBe("rejected");
  });

  it("edit & approve sends the edited arguments", () => {
    const editing = reduceCard(initialCardState, { type: "edit", approval: pending });
    expect(editing.mode).toBe("edit");
    const draft = { ...(editing.draft as EmailDraft), body: "Edited body\n\nBest,\nOlivia" };
    const state = reduceCard(editing, { type: "draft", draft });
    expect(decisionRequest(state, "edit")).toEqual({
      ok: true,
      body: { decision: "approve", arguments: { to: ["leadership@acme.test"], cc: [], subject: "Weekly Operations Report", body: "Edited body\n\nBest,\nOlivia" } },
    });
  });

  it("edits start from the approver's earlier edit when there is one", () => {
    const edited = approval({ edited_arguments: { to: ["ceo@acme.test"], cc: [], subject: "S", body: "B" } });
    expect((reduceCard(initialCardState, { type: "edit", approval: edited }).draft as EmailDraft).to).toEqual(["ceo@acme.test"]);
  });

  it("local problems stop the request and show on the form", () => {
    const editing = reduceCard(initialCardState, { type: "edit", approval: pending });
    const empty = reduceCard(editing, { type: "draft", draft: { ...(editing.draft as EmailDraft), to: [] } });
    const check = decisionRequest(empty, "edit");
    expect(check.ok).toBe(false);
    const shown = play([{ type: "submit", kind: "edit" }, { type: "invalid", errors: check.ok ? {} : check.errors }], empty);
    expect(shown).toMatchObject({ mode: "edit", submitting: null, draftErrors: { to: "Add at least one recipient." } });
  });

  it("409 not_pending closes the form and says it was already decided", () => {
    const state = play([
      { type: "edit", approval: pending },
      { type: "submit", kind: "edit" },
      { type: "failed", error: new ApiError({ status: 409, title: "Conflict", code: "not_pending", detail: null }) },
    ]);
    expect(state).toMatchObject({ mode: "view", draft: null, submitting: null, error: { kind: "not_pending", message: "This was already decided or has expired." } });
  });

  it("422 invalid_input keeps the edit open with the server's field errors", () => {
    const detail = "1 validation error for SendEmail\nto.0\n  String should match pattern '^x$' [type=string_pattern_mismatch, input_value='bob']";
    const state = play([
      { type: "edit", approval: pending },
      { type: "submit", kind: "edit" },
      { type: "failed", error: new ApiError({ status: 422, title: "The edited action is not valid", code: "invalid_input", detail }) },
    ]);
    expect(state.mode).toBe("edit");
    expect(state.error).toMatchObject({ kind: "invalid_input", fields: [{ field: "to", path: "to.0" }] });
    expect(serverFieldErrors(state.error)).toEqual({ to: "Not in the expected format." });
  });

  it("other errors keep the form and the error", () => {
    const state = play([{ type: "submit", kind: "approve" }, { type: "failed", error: new ApiError({ status: 0, title: "Network error", code: "network_error" }) }]);
    expect(state).toMatchObject({ mode: "view", submitting: null, error: { kind: "other" } });
  });

  it("ignores clicks while a decision is on its way (no double submit)", () => {
    const submitting = play([{ type: "submit", kind: "approve" }]);
    expect(reduceCard(submitting, { type: "submit", kind: "reject" })).toBe(submitting);
    expect(reduceCard(submitting, { type: "edit", approval: pending })).toBe(submitting);
  });

  it("a refetched, settled approval wins over the stored decision", () => {
    const executed = approval({ status: "executed" });
    expect(latestApproval(executed, approval({ status: "approved" }))).toBe(executed);
    expect(latestApproval(pending, null)).toBe(pending);
  });
});
