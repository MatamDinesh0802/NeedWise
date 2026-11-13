import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Source } from "@/lib/chat/api";

import { ApiError } from "@/lib/api";

import { isGone, reviewOverdue, SourcesPanel } from "./sources-panel";

const source = (overrides: Partial<Source> = {}): Source => ({
  marker: "S1",
  document_id: "d1",
  chunk_id: "k1",
  collection_id: "c1",
  title: "Travel policy",
  filename: "travel-policy.md",
  section: "Per diem",
  page: null,
  trust: "official",
  sensitivity: "internal",
  review_overdue: false,
  text: "Per diem is 60 EUR.",
  ...overrides,
});

const render = (sources: Source[]) =>
  renderToStaticMarkup(
    <SourcesPanel data={{ answerKey: "a1", sources, text: "Per diem is 60 EUR [S1].", citations: [{ marker: "S1" }] }} focused={null} onClose={() => {}} />,
  );

describe("SourcesPanel review warnings", () => {
  it("says when a cited official document is past its review date", () => {
    const html = render([source({ review_overdue: true })]);
    expect(html).toContain("Review overdue");
    expect(html).toContain("hasn&#x27;t confirmed it is still correct");
  });

  it("says nothing for documents reviewed on time, or from answers stored before reviews existed", () => {
    expect(render([source()])).not.toContain("Review overdue");
    expect(render([source({ review_overdue: undefined })])).not.toContain("Review overdue");
  });

  it("only warns about official documents", () => {
    expect(reviewOverdue({ trust: "reference", review_overdue: true })).toBe(false);
    expect(reviewOverdue({ trust: "official", review_overdue: true })).toBe(true);
  });
});

describe("SourcesPanel for a document that is gone", () => {
  it("treats 404 and 403 as deleted or no longer open, other failures as a load error", () => {
    const problem = (status: number) => new ApiError({ status, title: "x", code: null, detail: null, problem: null });
    expect(isGone(problem(404))).toBe(true);
    expect(isGone(problem(403))).toBe(true);
    expect(isGone(problem(500))).toBe(false);
    expect(isGone(new Error("offline"))).toBe(false);
  });

  it("offers the document while its passage is available", () => {
    expect(render([source()])).toContain("Open document");
  });
});
