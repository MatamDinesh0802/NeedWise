import { describe, expect, it } from "vitest";

import { topDrivers } from "@/test/analytics-fixtures";

import { csvField, csvFilename, toCsv } from "./csv";

describe("csvField", () => {
  it("writes raw values and quotes only when needed", () => {
    expect(csvField(1650000)).toBe("1650000");
    expect(csvField(-12.5)).toBe("-12.5");
    expect(csvField(null)).toBe("");
    expect(csvField(true)).toBe("true");
    expect(csvField("Bluefin Freight")).toBe("Bluefin Freight");
    expect(csvField('Nordlicht "Logistik", GmbH')).toBe('"Nordlicht ""Logistik"", GmbH"');
    expect(csvField("two\nlines")).toBe('"two\nlines"');
  });

  it("writes lists, maps and structs as one JSON field", () => {
    expect(csvField(["C001", "C002"])).toBe('"[""C001"",""C002""]"');
    expect(csvField({ open: 1, paid: 139 })).toBe('"{""open"":1,""paid"":139}"');
    expect(toCsv(["line", "customers"], [["AMR-200", ["C001", "C002"]]]).slice(1)).toBe('line,customers\r\nAMR-200,"[""C001"",""C002""]"\r\n');
  });

  it("neutralizes text that spreadsheets would run as a formula", () => {
    expect(csvField("=HYPERLINK(\"http://evil\")")).toBe('"\'=HYPERLINK(""http://evil"")"');
    expect(csvField("+1")).toBe("'+1");
    expect(csvField("-1")).toBe("'-1"); // text, not a number
    expect(csvField("@SUM(A1)")).toBe("'@SUM(A1)");
  });
});

describe("toCsv", () => {
  it("writes a BOM, a header and CRLF rows", () => {
    const csv = toCsv(
      topDrivers.columns.map((c) => c.name),
      topDrivers.rows,
    );
    expect(csv.startsWith("﻿")).toBe(true);
    expect(csv.slice(1).split("\r\n")).toEqual([
      "name,growth_eur",
      "Bluefin Freight,1650000",
      "Polaris Parcel,1320000",
      '"Nordlicht ""Logistik"", GmbH",410000.5',
      "",
    ]);
  });

  it("fills short rows with empty fields", () => {
    expect(toCsv(["a", "b"], [[1]]).slice(1)).toBe("a,b\r\n1,\r\n");
  });
});

describe("csvFilename", () => {
  it("names the file after the question and query", () => {
    expect(csvFilename("Monthly revenue trend this year and the top 3 drivers", "Q1")).toBe(
      "insights-q1-monthly-revenue-trend-this-year-and-the-top-3.csv", // cut at a word boundary
    );
    expect(csvFilename("Revenue by region", "Q2")).toBe("insights-q2-revenue-by-region.csv");
    expect(csvFilename("Umsatz für Q3 — Übersicht?", "Q2")).toBe("insights-q2-umsatz-fur-q3-ubersicht.csv");
    expect(csvFilename("???", "Q1")).toBe("insights-q1.csv");
  });
});
