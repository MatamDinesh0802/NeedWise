import { describe, expect, it } from "vitest";

import { formatDate } from "@/lib/format";

import type { OfficialRequest } from "./api";
import {
  demotedBy,
  documentLabel,
  escalationLabel,
  joinOr,
  noteError,
  officialBlocker,
  officialRequestSummary,
  overviewCounts,
  overviewIsClear,
  ownerText,
  reviewBadge,
  reviewState,
  reviewSummary,
  type GovernanceOverview,
} from "./governance";

const NOW = Date.parse("2026-11-12T12:00:00Z");
const day = (n: number) => new Date(NOW + n * 24 * 3600 * 1000).toISOString();
const priya = { id: "u-priya", display_name: "Priya Raman" };
const erin = { id: "u-erin", display_name: "Erin Takahashi" };

describe("reviewState and reviewBadge", () => {
  it("has nothing to say without a review cycle", () => {
    expect(reviewState({ next_review_at: null, review_overdue: false }, NOW)).toBe("none");
    expect(reviewBadge({ next_review_at: null, review_overdue: false }, NOW)).toBeNull();
  });

  it("is overdue past the review date, and says by how long", () => {
    expect(reviewState({ next_review_at: day(-3), review_overdue: true }, NOW)).toBe("overdue");
    expect(reviewBadge({ next_review_at: day(-3), review_overdue: true }, NOW)).toBe("Review overdue by 3 days");
    expect(reviewBadge({ next_review_at: day(-1), review_overdue: true }, NOW)).toBe("Review overdue by 1 day");
  });

  it("trusts the API's overdue flag even when the clocks disagree", () => {
    expect(reviewState({ next_review_at: day(0.01), review_overdue: true }, NOW)).toBe("overdue");
    expect(reviewBadge({ next_review_at: day(0.01), review_overdue: true }, NOW)).toBe("Review overdue");
  });

  it("is due soon within 30 days, and fine after that", () => {
    expect(reviewState({ next_review_at: day(12), review_overdue: false }, NOW)).toBe("due_soon");
    expect(reviewBadge({ next_review_at: day(12), review_overdue: false }, NOW)).toBe("Review due in 12 days");
    expect(reviewBadge({ next_review_at: day(0.2), review_overdue: false }, NOW)).toBe("Review due today");
    expect(reviewState({ next_review_at: day(45), review_overdue: false }, NOW)).toBe("ok");
    expect(reviewBadge({ next_review_at: day(45), review_overdue: false }, NOW)).toBeNull();
  });
});

describe("reviewSummary", () => {
  it("says when, by whom and when next", () => {
    const text = reviewSummary({ reviewed_at: day(-30), reviewed_by: priya, next_review_at: day(335), review_overdue: false });
    expect(text).toBe(`Reviewed ${formatDate(day(-30))} by Priya Raman; next review ${formatDate(day(335))}.`);
  });

  it("says 'you' for the signed-in reviewer", () => {
    const text = reviewSummary({ reviewed_at: day(-1), reviewed_by: priya, next_review_at: day(90), review_overdue: false }, "u-priya");
    expect(text).toContain("by you;");
  });

  it("handles a document never reviewed, and a collection without a cycle", () => {
    expect(reviewSummary({ reviewed_at: null, reviewed_by: null, next_review_at: day(20), review_overdue: false })).toBe(
      `Not reviewed yet; first review due ${formatDate(day(20))}.`,
    );
    expect(reviewSummary({ reviewed_at: null, reviewed_by: null, next_review_at: null, review_overdue: false })).toBe(
      "Not reviewed yet. This collection has no review cycle.",
    );
  });
});

describe("official requests", () => {
  const request: OfficialRequest = { requested_by: erin, requested_at: day(-2), note: "Approved by legal", approvers: [priya] };

  it("names who asked and who decides", () => {
    expect(officialRequestSummary(request)).toBe(`Requested by Erin Takahashi on ${formatDate(day(-2))}. Waiting for Priya Raman.`);
    expect(officialRequestSummary(request, "u-erin")).toContain("Requested by you");
    expect(officialRequestSummary(request, "u-priya")).toContain("Waiting for you.");
  });

  it("lists several approvers (org admins) with 'or'", () => {
    expect(joinOr(["Erin"])).toBe("Erin");
    expect(joinOr(["Erin", "Amara"])).toBe("Erin or Amara");
    expect(joinOr(["Erin", "Amara", "Sam"])).toBe("Erin, Amara or Sam");
    expect(joinOr([])).toBe("");
  });

  it("explains why a request can't be made", () => {
    expect(officialBlocker({ trust: "official", official_request: null }, true)).toBe("This document is already official.");
    expect(officialBlocker({ trust: "reference", official_request: request }, true)).toContain("has been requested");
    expect(officialBlocker({ trust: "reference", official_request: null }, false)).toContain("editors");
    expect(officialBlocker({ trust: "reference", official_request: null }, true)).toBeNull();
  });

  it("needs a reason, within the API's limit", () => {
    expect(noteError("   ", { required: true })).toContain("Say why");
    expect(noteError("", { required: false })).toBeNull();
    expect(noteError("x".repeat(2001), { required: false })).toContain("2,000");
    expect(noteError("Checked against the 2026 policy", { required: true })).toBeNull();
  });
});

describe("overview", () => {
  const collection = {
    id: "c1",
    name: "Board Papers",
    can_view: false,
    owner_id: null,
    review_interval_days: 365,
    review_escalation_days: 14,
    review_auto_demote_days: null,
  };
  const doc = {
    id: "d1",
    title: null,
    collection_id: "c1",
    trust: "official" as const,
    owner: priya,
    owner_inactive: false,
    reviewed_at: null,
    reviewed_by: null,
    next_review_at: day(-1),
    review_escalation: null,
    review_escalated_at: null,
  };
  const empty: GovernanceOverview = {
    scope: "all",
    collections: [collection],
    overdue: [],
    due_soon: [],
    without_owner: [],
    pending_official: [],
    recent_demotions: [],
  };

  it("withholds titles the caller can't open, and says where the document is", () => {
    expect(documentLabel(doc, collection)).toBe("A document in Board Papers you can't open");
    expect(documentLabel({ title: "Travel policy" }, collection)).toBe("Travel policy");
  });

  it("counts every list, and knows when nothing needs attention", () => {
    expect(overviewIsClear(empty)).toBe(true);
    const busy = { ...empty, overdue: [doc], without_owner: [doc, doc] };
    expect(overviewCounts(busy)).toEqual({ overdue: 1, due_soon: 0, without_owner: 2, pending_official: 0, recent_demotions: 0 });
    expect(overviewIsClear(busy)).toBe(false);
  });

  it("names each escalation stage, and who demoted a document", () => {
    expect(escalationLabel(null)).toBeNull();
    expect(escalationLabel("collection_owner")).toBe("Escalated to the collection's owner");
    expect(escalationLabel("admins")).toBe("Escalated to the administrators");
    expect(escalationLabel("demoted")).toBe("Demoted: review overdue");
    expect(demotedBy({ by: priya, reason: null })).toBe("Priya Raman");
    expect(demotedBy({ by: null, reason: "review_overdue" })).toBe("Automatic: review overdue");
    expect(demotedBy({ by: null, reason: "new_version" })).toBe("Automatic: new content");
    expect(demotedBy({ by: null })).toBe("Someone");
  });

  it("marks owners who left", () => {
    expect(ownerText({ owner: null, owner_inactive: false })).toBe("No owner");
    expect(ownerText({ owner: priya, owner_inactive: true })).toBe("Priya Raman (left)");
    expect(ownerText({ owner: priya, owner_inactive: false })).toBe("Priya Raman");
  });
});
