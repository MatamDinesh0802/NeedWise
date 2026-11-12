import { describe, expect, it } from "vitest";

import {
  addDays,
  expectedPeriods,
  intervalFor,
  isoDay,
  parseDay,
  periodLabel,
  presetRange,
  rangeDays,
  rangeError,
  rangeText,
} from "./dates";

const now = new Date("2026-11-12T15:30:00Z");

describe("presetRange", () => {
  it("this month runs from the 1st to today", () => {
    expect(presetRange("this_month", now)).toEqual({ from: "2026-11-01", to: "2026-11-12" });
  });

  it("last 30 days includes today", () => {
    const range = presetRange("last_30_days", now);
    expect(range).toEqual({ from: "2026-10-14", to: "2026-11-12" });
    expect(rangeDays(range)).toBe(30);
  });

  it("last month is the whole previous calendar month, across a year boundary too", () => {
    expect(presetRange("last_month", now)).toEqual({ from: "2026-10-01", to: "2026-10-31" });
    expect(presetRange("last_month", new Date("2027-01-03T00:00:00Z"))).toEqual({ from: "2026-12-01", to: "2026-12-31" });
    expect(presetRange("last_month", new Date("2028-03-10T00:00:00Z"))).toEqual({ from: "2028-02-01", to: "2028-02-29" });
  });

  it("uses the UTC calendar day", () => {
    expect(isoDay(new Date("2026-11-30T23:59:00Z"))).toBe("2026-11-30");
    expect(presetRange("this_month", new Date("2026-12-01T00:10:00Z"))).toEqual({ from: "2026-12-01", to: "2026-12-01" });
  });
});

describe("days", () => {
  it("parses only real calendar days", () => {
    expect(parseDay("2026-02-29")).toBeNull();
    expect(parseDay("2026-13-01")).toBeNull();
    expect(parseDay("11/12/2026")).toBeNull();
    expect(parseDay("2028-02-29")?.toISOString()).toBe("2028-02-29T00:00:00.000Z");
  });

  it("adds days across months", () => {
    expect(addDays("2026-10-31", 1)).toBe("2026-11-01");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
  });

  it("counts both ends of a range", () => {
    expect(rangeDays({ from: "2026-11-01", to: "2026-11-01" })).toBe(1);
    expect(rangeDays({ from: "2026-11-05", to: "2026-11-01" })).toBe(0);
  });
});

describe("rangeError", () => {
  it.each([
    [{ from: "", to: "2026-11-01" }, "Choose both a start and an end date."],
    [{ from: "2026-11-31", to: "2026-12-01" }, "Enter dates as YYYY-MM-DD."],
    [{ from: "2026-11-05", to: "2026-11-01" }, "The end date is before the start date."],
    [{ from: "2026-11-01", to: "2026-11-01" }, null],
    // The API refuses more than 400 days: say so before asking it.
    [{ from: "2025-01-01", to: "2026-11-12" }, "Choose at most 400 days."],
    [{ from: "2025-10-09", to: "2026-11-12" }, null],
  ])("%j → %s", (range, message) => {
    expect(rangeError(range)).toBe(message);
  });
});

describe("intervalFor", () => {
  it("is daily for a month, weekly for a quarter, monthly for a year", () => {
    expect(intervalFor({ from: "2026-11-01", to: "2026-11-30" })).toBe("day");
    expect(intervalFor({ from: "2026-08-01", to: "2026-10-31" })).toBe("week");
    expect(intervalFor({ from: "2025-11-01", to: "2026-10-31" })).toBe("month");
  });
});

describe("expectedPeriods", () => {
  it("lists every day, and every month start", () => {
    expect(expectedPeriods({ from: "2026-10-30", to: "2026-11-02" }, "day")).toEqual(["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02"]);
    expect(expectedPeriods({ from: "2026-10-15", to: "2027-01-02" }, "month")).toEqual(["2026-10-01", "2026-11-01", "2026-12-01", "2027-01-01"]);
  });

  it("lists every week from the Monday on or before the start, as the API counts weeks", () => {
    const weeks = expectedPeriods({ from: "2026-08-01", to: "2026-11-12" }, "week")!;
    expect(weeks).toHaveLength(16);
    expect(weeks[0]).toBe("2026-07-27"); // Aug 1, 2026 is a Saturday
    expect(weeks.at(-1)).toBe("2026-11-09");
    expect(expectedPeriods({ from: "2026-11-09", to: "2026-11-09" }, "week")).toEqual(["2026-11-09"]);
    expect(expectedPeriods({ from: "2026-11-05", to: "2026-11-01" }, "day")).toBeNull();
  });
});

describe("labels", () => {
  it("names periods by interval", () => {
    expect(periodLabel("2026-11-03", "day")).toBe("Nov 3");
    expect(periodLabel("2026-11-02", "week")).toBe("Week of Nov 2");
    expect(periodLabel("2026-11-01", "month")).toBe("Nov 2026");
    expect(periodLabel("2026-11-01T00:00:00Z", "month")).toBe("Nov 2026");
    expect(periodLabel("soon", "day")).toBe("soon");
  });

  it("writes both ends of a range in full", () => {
    expect(rangeText({ from: "2026-10-14", to: "2026-11-12" })).toBe("Oct 14, 2026 – Nov 12, 2026");
    expect(rangeText({ from: "2026-11-12", to: "2026-11-12" })).toBe("Nov 12, 2026");
    expect(rangeText({ from: "x", to: "2026-11-12" })).toBe("");
  });
});
