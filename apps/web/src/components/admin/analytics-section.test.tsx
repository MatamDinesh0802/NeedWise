import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { AdminAnalytics, DatasetTable } from "@/lib/analytics/api";

import { DatasetPanel, ModelEditor } from "./analytics-section";

const customers: DatasetTable = {
  name: "customers",
  rows: 24,
  columns: [
    { name: "customer_id", type: "VARCHAR" },
    { name: "name", type: "VARCHAR" },
    { name: "customer_since", type: "DATE" },
  ],
};
const invoiceLines: DatasetTable = {
  name: "invoice_lines",
  rows: 1478,
  columns: Array.from({ length: 10 }, (_, i) => ({ name: `col_${i}`, type: "BIGINT" })),
};

const data = (overrides: Partial<AdminAnalytics["dataset"]> = {}): AdminAnalytics => ({
  yaml: "name: Acme\n",
  dataset: { version: "3", tables: [customers, invoiceLines], ...overrides },
});

describe("DatasetPanel", () => {
  it("lists tables with row counts, columns and a labelled delete button", () => {
    const html = renderToStaticMarkup(<DatasetPanel data={data()} />);
    expect(html).toContain("version 3");
    expect(html).toContain("24 rows · 3 columns");
    expect(html).toContain("1,478 rows · 10 columns");
    expect(html).toContain("customer_since <span");
    expect(html).toContain('aria-label="Delete table customers"');
    expect(html).toContain("Show all 10 columns");
  });

  it("offers the upload form with a labelled file input that accepts CSV and XLSX", () => {
    const html = renderToStaticMarkup(<DatasetPanel data={data()} />);
    expect(html).toMatch(/<label for="([^"]+)"[^>]*>File<\/label><input id="\1"[^>]*type="file"/);
    expect(html).toContain('accept=".csv,.xlsx,');
    expect(html).toContain("Table name");
    expect(html).toContain("Upload table");
  });

  it("says when there are no tables yet", () => {
    const html = renderToStaticMarkup(<DatasetPanel data={data({ version: null, tables: [] })} />);
    expect(html).toContain("No tables yet");
    expect(html).not.toContain("version");
  });
});

describe("ModelEditor", () => {
  it("edits the YAML in a labelled monospace box, saving only when changed", () => {
    const html = renderToStaticMarkup(<ModelEditor yaml={"name: Acme\n"} tables={[customers]} />);
    expect(html).toMatch(/<label[^>]*for="([^"]+)"[^>]*>Semantic model \(YAML\)<\/label><textarea[^>]*id="\1"/);
    expect(html).toContain("font-mono");
    expect(html).toContain("name: Acme");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>.*Save model<\/button>/);
    expect(html).not.toContain("Unsaved changes");
    expect(html).not.toContain("Start from the uploaded tables</button>");
  });

  it("explains that Insights is off without a model and offers a starting point", () => {
    const html = renderToStaticMarkup(<ModelEditor yaml={null} tables={[customers]} />);
    expect(html).toContain("No data model yet: Insights stays off for everyone until you save one.");
    expect(html).toContain("Start from the uploaded tables</button>");
  });
});
