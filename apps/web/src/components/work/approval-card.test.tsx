import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Approval } from "@/lib/work/api";

import { ApprovalCard } from "./approval-card";

const approval = (overrides: Partial<Approval> = {}): Approval => ({
  id: "ap1",
  tool: "send_email",
  summary: "Send an email to leadership@acme.test: Weekly Operations Report",
  arguments: { to: ["leadership@acme.test"], cc: [], subject: "Weekly Operations Report", body: "Safety: no incidents." },
  edited_arguments: null,
  status: "pending",
  requested_by: "u-olivia",
  approver_id: "u-olivia",
  decided_at: null,
  decision_note: null,
  expires_at: "2999-01-01T00:00:00Z",
  result: null,
  origin: { conversation_id: "c1", run_id: "r1" },
  created_at: "2026-11-04T09:59:00Z",
  ...overrides,
});

describe("ApprovalCard", () => {
  it("makes clear nothing happened yet, and gives the approver all three choices", () => {
    const html = renderToStaticMarkup(<ApprovalCard approval={approval()} meId="u-olivia" />);
    expect(html).toContain("Nothing has happened yet.");
    expect(html).toContain("This only runs if you approve it.");
    expect(html).toContain("Approve and send");
    expect(html).toContain("Edit &amp; approve");
    expect(html).toContain("Reject");
    expect(html).toContain("leadership@acme.test");
    expect(html).toContain("Weekly Operations Report");
  });

  it("shows no buttons to someone who isn't the approver", () => {
    const html = renderToStaticMarkup(<ApprovalCard approval={approval({ approver_id: "u-olivia", requested_by: "u-erin" })} meId="u-erin" />);
    expect(html).toContain("It only runs once the approver says yes.");
    expect(html).not.toContain("Approve and send");
    expect(html).toContain("Waiting for the approver");
  });

  it("shows the result once executed, and that it was edited", () => {
    const html = renderToStaticMarkup(
      <ApprovalCard
        approval={approval({
          status: "executed",
          decided_at: "2026-11-04T10:01:00Z",
          edited_arguments: { to: ["leadership@acme.test"], cc: [], subject: "Weekly Operations Report", body: "Safety: no incidents.\n\nBest,\nOlivia" },
          result: { content: "Sent to leadership@acme.test." },
        })}
        meId="u-olivia"
      />,
    );
    expect(html).toContain("Sent to leadership@acme.test.");
    expect(html).toContain("Approved with changes.");
    expect(html).toContain("Edited by the approver before it ran");
    expect(html).toContain("Best,\nOlivia");
    expect(html).not.toContain("Nothing has happened yet.");
    expect(html).not.toContain("Approve and send");
  });

  it("explains a failure and a rejection", () => {
    expect(renderToStaticMarkup(<ApprovalCard approval={approval({ status: "failed", result: { error: "upstream_unavailable: SMTP down" } })} meId="u-olivia" />)).toContain(
      "It didn&#x27;t run: upstream_unavailable: SMTP down",
    );
    expect(renderToStaticMarkup(<ApprovalCard approval={approval({ status: "rejected", decision_note: "Not final" })} meId="u-olivia" />)).toContain(
      "Rejected: “Not final”. Nothing ran.",
    );
  });

  describe("an official-status request (knowledge governance)", () => {
    const official = (overrides: Partial<Approval> = {}) =>
      approval({
        tool: "knowledge.official_status",
        summary: "Make “Travel policy” official",
        arguments: { document_id: "d-1", title: "Travel policy", collection_id: "c-1", collection: "HR Policies", note: "Signed off by HR" },
        requested_by: "u-erin",
        approver_id: "u-priya",
        origin: { via: "governance", kind: "official_status", request_id: "r-1", document_id: "d-1" },
        ...overrides,
      });

    it("says what approving and rejecting mean, shows the reason, and can't be edited", () => {
      const html = renderToStaticMarkup(<ApprovalCard approval={official()} meId="u-priya" />);
      expect(html).toContain("Official status for a document");
      expect(html).toContain("Approving makes the document official");
      expect(html).toContain("Rejecting keeps its current trust level");
      expect(html).toContain("Travel policy");
      expect(html).toContain("HR Policies");
      expect(html).toContain("Signed off by HR");
      expect(html).toContain("Approve: make it official");
      expect(html).toContain('href="/knowledge/documents/d-1"');
      expect(html).toContain("Requested in the Knowledge Hub");
      expect(html).not.toContain("Edit &amp; approve");
    });

    it("tells the requester who decides, and offers no buttons", () => {
      const html = renderToStaticMarkup(<ApprovalCard approval={official()} meId="u-erin" />);
      expect(html).not.toContain("Approve: make it official");
      expect(html).toContain("It only runs once");
    });

    it("reports the outcome, including when another approver decided first", () => {
      expect(renderToStaticMarkup(<ApprovalCard approval={official({ status: "executed", decided_at: "2026-11-05T10:00:00Z" })} meId="u-priya" />)).toContain(
        "The document is now official.",
      );
      const superseded = renderToStaticMarkup(<ApprovalCard approval={official({ status: "superseded", decided_at: "2026-11-05T10:00:00Z" })} meId="u-priya" />);
      expect(superseded).toContain("Decided by another approver");
      expect(superseded).toContain("Another approver decided this request first.");
    });

    it("says when the request went to someone else because the collection's owner changed", () => {
      const reason = "The collection's owner changed: the request went to whoever decides now.";
      const html = renderToStaticMarkup(
        <ApprovalCard approval={official({ status: "superseded", decided_at: "2026-11-05T10:00:00Z", result: { reason } })} meId="u-priya" />,
      );
      expect(html).toContain("Reassigned");
      expect(html).not.toContain("Decided by another approver");
      expect(html).toContain("owner changed: the request went to whoever decides now. Nothing more to do.");
    });

    it("shows the version, and withholds the title, the note and the link from an approver who can't open the collection", () => {
      const pinned = renderToStaticMarkup(
        <ApprovalCard approval={official({ arguments: { ...official().arguments, version: 2 } })} meId="u-priya" />,
      );
      expect(pinned).toContain("Version 2");

      const withheld = official({
        summary: "Make a document in “HR Guidance (internal)” official",
        arguments: { document_id: "d-1", collection_id: "c-1", collection: "HR Guidance (internal)", version: 1, withheld: true },
      });
      const html = renderToStaticMarkup(<ApprovalCard approval={withheld} meId="u-priya" />);
      expect(html).toContain("A document in HR Guidance (internal) you can&#x27;t open");
      expect(html).not.toContain("Travel policy");
      expect(html).not.toContain("Signed off by HR");
      expect(html).not.toContain('href="/knowledge/documents/d-1"');
    });
  });

  it("shows the step of a chain, an escalation, and where a forwarded step went", () => {
    const first = approval({
      status: "forwarded",
      approver_id: "u-marcus",
      requested_by: "u-bob",
      stage: 1,
      stages: 2,
      rule: "a manager of the requester's department, then hannah.schmidt@acme.test",
      result: { stage: 2, stages: 2, next: ["ap2"] },
    });
    const html = renderToStaticMarkup(<ApprovalCard approval={first} meId="u-bob" />);
    expect(html).toContain("Step 1 of 2: a manager of the requester&#x27;s department, then hannah.schmidt@acme.test");
    expect(html).toContain("Approved, with the next approver");
    expect(html).toContain("Step 2 decides next; nothing runs until the last step approves.");
    expect(html).toContain('href="/approvals/ap2"');
    expect(html).not.toContain("Approve and send");

    const escalated = approval({ approver_id: "u-ivan", escalated_from: "ap0" });
    expect(renderToStaticMarkup(<ApprovalCard approval={escalated} meId="u-ivan" />)).toContain("Escalated to you");
  });

  it("falls back to a JSON view for tools it doesn't know", () => {
    const html = renderToStaticMarkup(<ApprovalCard approval={approval({ tool: "erp.update_stock", arguments: { sku: "A1", qty: 3 } })} meId="u-olivia" />);
    expect(html).toContain("Use erp.update_stock");
    expect(html).toContain("&quot;sku&quot;: &quot;A1&quot;");
  });
});
