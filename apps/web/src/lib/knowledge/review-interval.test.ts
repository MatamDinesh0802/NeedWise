import { describe, expect, it } from "vitest";

import { intervalError, intervalText, parseInterval } from "./review-interval";

describe("review interval", () => {
  it("treats an empty field as no reviews", () => {
    expect(intervalError("  ")).toBeNull();
    expect(parseInterval("")).toBeNull();
    expect(intervalText(null)).toBe("No reviews");
  });

  it("accepts whole days within the API's range", () => {
    expect(intervalError("365")).toBeNull();
    expect(parseInterval(" 365 ")).toBe(365);
    expect(intervalText(365)).toBe("Reviewed every 365 days");
    expect(intervalText(1)).toBe("Reviewed every 1 day");
  });

  it("refuses fractions, words and out-of-range values", () => {
    expect(intervalError("90.5")).toContain("whole number");
    expect(intervalError("a year")).toContain("whole number");
    expect(intervalError("0")).toBe("Between 1 and 3,650 days.");
    expect(intervalError("3651")).toBe("Between 1 and 3,650 days.");
  });
});
