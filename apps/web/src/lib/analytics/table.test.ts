import { describe, expect, it } from "vitest";

import type { Cell, ResultColumn } from "./api";
import { ariaSort, nextSort, sortRows } from "./table";

const columns: ResultColumn[] = [
  { name: "name", type: "text", format: "text" },
  { name: "revenue", type: "decimal", format: "eur" },
  { name: "since", type: "date", format: "date" },
];
const rows: Cell[][] = [
  ["Polaris", 1320000, "2026-01-01"],
  ["bluefin", null, "2025-06-01"],
  ["Atlas", "200000", null],
  ["Zephyr", 1650000, "2026-03-01"],
];

describe("sortRows", () => {
  it("sorts numbers numerically (numeric strings too), nulls last both ways", () => {
    expect(sortRows(rows, columns, { column: 1, direction: "desc" }).map((r) => r[0])).toEqual(["Zephyr", "Polaris", "Atlas", "bluefin"]);
    expect(sortRows(rows, columns, { column: 1, direction: "asc" }).map((r) => r[0])).toEqual(["Atlas", "Polaris", "Zephyr", "bluefin"]);
  });

  it("sorts text case-insensitively and ISO dates chronologically", () => {
    expect(sortRows(rows, columns, { column: 0, direction: "asc" }).map((r) => r[0])).toEqual(["Atlas", "bluefin", "Polaris", "Zephyr"]);
    expect(sortRows(rows, columns, { column: 2, direction: "asc" }).map((r) => r[0])).toEqual(["bluefin", "Polaris", "Zephyr", "Atlas"]);
  });

  it("keeps the original order without a sort, and doesn't mutate", () => {
    const copy = sortRows(rows, columns, null);
    expect(copy).toEqual(rows);
    expect(copy).not.toBe(rows);
  });
});

describe("nextSort", () => {
  it("starts numbers descending and text ascending, then flips, then clears", () => {
    expect(nextSort(null, 1, true)).toEqual({ column: 1, direction: "desc" });
    expect(nextSort({ column: 1, direction: "desc" }, 1, true)).toEqual({ column: 1, direction: "asc" });
    expect(nextSort({ column: 1, direction: "asc" }, 1, true)).toBeNull();
    expect(nextSort(null, 0, false)).toEqual({ column: 0, direction: "asc" });
    expect(nextSort({ column: 1, direction: "asc" }, 0, false)).toEqual({ column: 0, direction: "asc" });
  });

  it("reports aria-sort", () => {
    expect(ariaSort({ column: 1, direction: "desc" }, 1)).toBe("descending");
    expect(ariaSort({ column: 1, direction: "asc" }, 1)).toBe("ascending");
    expect(ariaSort({ column: 1, direction: "asc" }, 0)).toBe("none");
    expect(ariaSort(null, 0)).toBe("none");
  });
});
