import { describe, expect, it } from "vitest";

import { demoteError, escalationError, escalationText, intervalError, intervalText, parseInterval } from "./review-interval";

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

describe("escalation of overdue official reviews", () => {
  it("needs a grace period between 1 and 365 days", () => {
    expect(escalationError("14")).toBeNull();
    expect(escalationError("")).toContain("every collection escalates");
    expect(escalationError("0")).toBe("Between 1 and 365 days.");
    expect(escalationError("366")).toBe("Between 1 and 365 days.");
    expect(escalationError("two weeks")).toContain("whole number");
  });

  it("demotes only when a period is set", () => {
    expect(demoteError("")).toBeNull();
    expect(demoteError("30")).toBeNull();
    expect(demoteError("0")).toBe("Between 1 and 3,650 days.");
    expect(demoteError("3651")).toBe("Between 1 and 3,650 days.");
  });

  it("says what happens in words", () => {
    expect(escalationText(14, null)).toBe("Overdue official reviews escalate after 14 days; never demoted automatically.");
    expect(escalationText(1, 1)).toBe("Overdue official reviews escalate after 1 day; demoted 1 day after the administrators are told.");
  });
});
