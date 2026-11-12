import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { GovernanceOverview, GovernedDocument } from "@/lib/knowledge/governance";

import { GovernanceOverviewView } from "./governance-overview";

const NOW = Date.parse("2026-11-12T12:00:00Z");
const day = (n: number) => new Date(NOW + n * 24 * 3600 * 1000).toISOString();
const priya = { id: "u-priya", display_name: "Priya Raman" };
const erin = { id: "u-erin", display_name: "Erin Takahashi" };

const doc = (overrides: Partial<GovernedDocument> = {}): GovernedDocument => ({
  id: "d-travel",
  title: "Travel policy",
  collection_id: "c-hr",
  trust: "official",
  owner: priya,
  owner_inactive: false,
  reviewed_at: day(-400),
  reviewed_by: priya,
  next_review_at: day(-35),
  ...overrides,
});

const overview = (overrides: Partial<GovernanceOverview> = {}): GovernanceOverview => ({
  scope: "all",
  collections: [
    { id: "c-hr", name: "HR Policies", can_view: true, owner_id: "u-priya", review_interval_days: 365 },
    { id: "c-board", name: "Board Papers", can_view: false, owner_id: null, review_interval_days: 180 },
  ],
  overdue: [doc(), doc({ id: "d-board", title: null, collection_id: "c-board", trust: "reference", owner: null, next_review_at: day(-3) })],
  due_soon: [doc({ id: "d-leave", title: "Leave policy", next_review_at: day(9) })],
  without_owner: [doc({ id: "d-old", title: "Old handbook", owner: erin, owner_inactive: true })],
  pending_official: [
    {
      document: doc({ id: "d-exp", title: "Expense policy", trust: "reference" }),
      requested_by: erin,
      requested_at: day(-2),
      note: "Signed off by finance",
      approvers: [priya],
    },
  ],
  recent_demotions: [
    { document_id: "d-gifts", title: "Gifts policy", collection_id: "c-hr", by: erin, at: day(-5), to: "reference", note: "Superseded" },
  ],
  ...overrides,
});

const render = (o: GovernanceOverview) => renderToStaticMarkup(<GovernanceOverviewView overview={o} now={NOW} />);

describe("GovernanceOverviewView", () => {
  it("counts each list and links to it", () => {
    const html = render(overview());
    expect(html).toMatch(/href="#[^"]+-overdue"[^>]*>.*Overdue reviews.*>2<\/span>/);
    expect(html).toMatch(/href="#[^"]+-pending_official"[^>]*>.*Waiting for official approval.*>1<\/span>/);
    expect(html).toContain("All 2 collections in the organization.");
  });

  it("links documents the caller can open, and withholds the others' titles", () => {
    const html = render(overview());
    expect(html).toContain('href="/knowledge/documents/d-travel"');
    expect(html).toContain("A document in Board Papers you can&#x27;t open");
    expect(html).not.toContain('href="/knowledge/documents/d-board"');
    expect(html).toContain("35 days ago");
    expect(html).toContain("in 9 days");
  });

  it("shows owners who left, pending requests and demotions with their notes", () => {
    const html = render(overview());
    expect(html).toContain("Erin Takahashi (left)");
    expect(html).toContain("Signed off by finance");
    expect(html).toContain("Superseded");
    expect(html).toContain("Gifts policy");
  });

  it("says when a list is empty", () => {
    const html = render(overview({ overdue: [], due_soon: [], without_owner: [], pending_official: [], recent_demotions: [] }));
    expect(html).toContain("No review is overdue.");
    expect(html).toContain("Every document has an owner.");
    expect(html).toContain("No request is waiting.");
  });

  it("explains an empty overview to someone who owns no collection", () => {
    const html = render(overview({ scope: "owned", collections: [], overdue: [], due_soon: [], without_owner: [], pending_official: [], recent_demotions: [] }));
    expect(html).toContain("You don&#x27;t own any collections");
  });

  it("names the owner's collections", () => {
    const html = render(overview({ scope: "owned", collections: [overview().collections[0]] }));
    expect(html).toContain("The collection you own: HR Policies.");
  });
});
