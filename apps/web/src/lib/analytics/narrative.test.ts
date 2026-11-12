import { describe, expect, it } from "vitest";

import { answer } from "@/test/analytics-fixtures";

import type { VerifiedNumber } from "./api";
import { referencedQueries, splitNarrative, unplacedNumbers, verificationSummary } from "./narrative";

const n = (text: string, verified = true, query: string | null = "Q1"): VerifiedNumber => ({ text, value: 0, verified, query });

describe("splitNarrative", () => {
  it("finds query markers and checked numbers in order", () => {
    const segments = splitNarrative("Revenue was €5.02M [Q1], up 12% [Q1, Q2].", [n("€5.02M"), n("12%", false, null)]);
    expect(segments).toEqual([
      { type: "text", value: "Revenue was " },
      { type: "number", text: "€5.02M", index: 0 },
      { type: "text", value: " " },
      { type: "queries", ids: ["Q1"] },
      { type: "text", value: ", up " },
      { type: "number", text: "12%", index: 1 },
      { type: "text", value: " " },
      { type: "queries", ids: ["Q1", "Q2"] },
      { type: "text", value: "." },
    ]);
  });

  it("does not match numbers inside longer numbers or words", () => {
    const segments = splitNarrative("Q5 grew 112% and 12.5% and 1,12% but 12% counts.", [n("12%"), n("5")]);
    expect(segments.filter((s) => s.type === "number")).toEqual([{ type: "number", text: "12%", index: 0 }]);
  });

  it("prefers the longest text, so €1.5M is not split into 1.5", () => {
    const segments = splitNarrative("It was €1.5M.", [n("1.5"), n("€1.5M")]);
    expect(segments.filter((s) => s.type === "number")).toEqual([{ type: "number", text: "€1.5M", index: 1 }]);
  });

  it("maps repeated texts to their entries in order, reusing the last one", () => {
    const numbers = [n("€2M", true), n("€2M", false, null)];
    const segments = splitNarrative("€2M then €2M then €2M", numbers).filter((s) => s.type === "number");
    expect(segments.map((s) => s.type === "number" && s.index)).toEqual([0, 1, 1]);
  });

  it("dedupes repeated ids in one marker and works without numbers", () => {
    expect(splitNarrative("See [Q1, Q1].", [])).toEqual([
      { type: "text", value: "See " },
      { type: "queries", ids: ["Q1"] },
      { type: "text", value: "." },
    ]);
  });
});

describe("referencedQueries and unplacedNumbers", () => {
  it("lists queries in first-appearance order", () => {
    expect(referencedQueries(answer().narrative)).toEqual(["Q1", "Q2"]);
  });

  it("finds checked numbers that the text never shows", () => {
    expect(unplacedNumbers("Revenue was €5.02M.", [n("€5.02M"), n("€4.9M")])).toEqual([1]);
    expect(unplacedNumbers(answer().narrative, answer().verification.numbers)).toEqual([]);
  });
});

describe("verificationSummary", () => {
  it.each([
    [0, 0, "The text states no figures to check."],
    [3, 3, "All 3 figures in the text match the query results."],
    [1, 1, "The one figure in the text matches the query results."],
    [4, 5, "4 of 5 figures in the text match the query results; 1 could not be matched."],
    [0, 2, "None of the 2 figures in the text could be matched to the query results."],
  ])("%d of %d", (verified, total, text) => {
    expect(verificationSummary(verified, total)).toBe(text);
  });
});
