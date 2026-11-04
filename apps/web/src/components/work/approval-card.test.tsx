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

  it("falls back to a JSON view for tools it doesn't know", () => {
    const html = renderToStaticMarkup(<ApprovalCard approval={approval({ tool: "erp.update_stock", arguments: { sku: "A1", qty: 3 } })} meId="u-olivia" />);
    expect(html).toContain("Use erp.update_stock");
    expect(html).toContain("&quot;sku&quot;: &quot;A1&quot;");
  });
});
