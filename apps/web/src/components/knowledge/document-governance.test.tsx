import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { formatDate } from "@/lib/format";
import type { DocumentDetail } from "@/lib/knowledge/api";

import { ReviewCell } from "./collection-view";
import { DocumentGovernance } from "./document-governance";

const NOW = Date.parse("2026-11-12T12:00:00Z");
const day = (n: number) => new Date(NOW + n * 24 * 3600 * 1000).toISOString();
const priya = { id: "u-priya", display_name: "Priya Raman" };
const erin = { id: "u-erin", display_name: "Erin Takahashi" };

const doc = (overrides: Partial<DocumentDetail> = {}): DocumentDetail => ({
  id: "d1",
  collection_id: "c1",
  title: "Travel policy",
  filename: "travel-policy.md",
  mime_type: "text/markdown",
  status: "ready",
  error: null,
  trust: "reference",
  sensitivity: "internal",
  chunk_count: 4,
  size_bytes: 1200,
  version: 1,
  updated_at: day(-40),
  owner: priya,
  reviewed_at: day(-40),
  reviewed_by: priya,
  next_review_at: day(325),
  review_overdue: false,
  official_request: null,
  versions: [],
  can_edit: true,
  can_review: true,
  can_change_owner: true,
  ...overrides,
});

const render = (d: DocumentDetail, meId: string | null = "u-erin") => renderToStaticMarkup(<DocumentGovernance doc={d} meId={meId} now={NOW} />);

describe("DocumentGovernance", () => {
  it("shows the owner, the last review and the next one, with the actions an editor has", () => {
    const html = render(doc());
    expect(html).toContain("Priya Raman");
    expect(html).toContain(`Reviewed ${formatDate(day(-40))} by Priya Raman; next review ${formatDate(day(325))}.`);
    expect(html).toContain("Change owner");
    expect(html).toContain("Mark reviewed");
    expect(html).toContain("Request official status");
    expect(html).not.toContain("Remove official status");
  });

  it("flags an overdue review", () => {
    const html = render(doc({ next_review_at: day(-10), review_overdue: true }));
    expect(html).toContain("Review overdue by 10 days");
  });

  it("shows a pending request with who asked, who decides and why, and no second request", () => {
    const html = render(
      doc({ official_request: { requested_by: erin, requested_at: day(-1), note: "Signed off by HR", approvers: [priya] } }),
    );
    expect(html).toContain("Official status requested");
    expect(html).toContain(`Requested by you on ${formatDate(day(-1))}. Waiting for Priya Raman.`);
    expect(html).toContain("Signed off by HR");
    expect(html).not.toContain("Request official status</button>");
  });

  it("lets an editor remove official status, which needs no approval", () => {
    const html = render(doc({ trust: "official" }));
    expect(html).toContain("Remove official status");
    expect(html).toContain("answers rank it first");
    expect(html).not.toContain("Request official status");
  });

  it("gives a viewer who owns the document only Mark reviewed", () => {
    const html = render(doc({ can_edit: false, can_change_owner: false, can_review: true }), "u-priya");
    expect(html).toContain("Priya Raman<span class=\"text-muted-foreground\"> (you)</span>");
    expect(html).toContain("Mark reviewed");
    expect(html).not.toContain("Change owner");
    expect(html).not.toContain("Request official status");
  });

  it("says what a missing owner and a missing review cycle mean", () => {
    const html = render(doc({ owner: null, reviewed_at: null, reviewed_by: null, next_review_at: null, can_review: false }));
    expect(html).toContain("No owner. Nobody is reminded to review it.");
    expect(html).toContain("Not reviewed yet. This collection has no review cycle.");
    expect(html).not.toContain("Mark reviewed");
  });
});

describe("ReviewCell (collection list)", () => {
  it("stands out when overdue or due soon, else shows the date", () => {
    expect(renderToStaticMarkup(<ReviewCell doc={doc({ next_review_at: day(-2), review_overdue: true })} now={NOW} />)).toContain(
      "Review overdue by 2 days",
    );
    expect(renderToStaticMarkup(<ReviewCell doc={doc({ next_review_at: day(5) })} now={NOW} />)).toContain("Review due in 5 days");
    expect(renderToStaticMarkup(<ReviewCell doc={doc()} now={NOW} />)).toContain(`Next ${formatDate(day(325))}`);
    expect(renderToStaticMarkup(<ReviewCell doc={doc({ next_review_at: null })} now={NOW} />)).toContain("—");
  });
});
