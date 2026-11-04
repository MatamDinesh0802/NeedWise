import { describe, expect, it } from "vitest";

import { citedMarkers, markerSortKey, splitCitations } from "./citations";

describe("splitCitations", () => {
  it("turns single and grouped markers into chips", () => {
    expect(splitCitations("You get 16 weeks [S1]. Contractors are excluded [S1, S3].")).toEqual([
      { type: "text", value: "You get 16 weeks " },
      { type: "cite", markers: ["S1"] },
      { type: "text", value: ". Contractors are excluded " },
      { type: "cite", markers: ["S1", "S3"] },
      { type: "text", value: "." },
    ]);
  });

  it("tolerates spacing and duplicates inside a group", () => {
    expect(splitCitations("[S2,S2 ,  S4]")).toEqual([{ type: "cite", markers: ["S2", "S4"] }]);
  });

  it("leaves things that only look similar", () => {
    for (const text of ["[s1]", "[S]", "[S1 S2]", "[Section 1]", "S1", "[S1, ]", "[1]"]) {
      expect(splitCitations(text), text).toEqual([{ type: "text", value: text }]);
    }
  });

  it("handles text with no markers and empty text", () => {
    expect(splitCitations("No sources here.")).toEqual([{ type: "text", value: "No sources here." }]);
    expect(splitCitations("")).toEqual([]);
  });

  it("is safe to call repeatedly (no shared regex state)", () => {
    expect(splitCitations("a [S1]")).toHaveLength(2);
    expect(splitCitations("a [S1]")).toHaveLength(2);
  });
});

describe("citedMarkers / markerSortKey", () => {
  it("lists cited markers in first-appearance order", () => {
    expect(citedMarkers("x [S3] y [S1, S3] z [S2]")).toEqual(["S3", "S1", "S2"]);
  });

  it("sorts S10 after S9", () => {
    expect(["S10", "S2", "S9"].sort((a, b) => markerSortKey(a) - markerSortKey(b))).toEqual(["S2", "S9", "S10"]);
  });
});
