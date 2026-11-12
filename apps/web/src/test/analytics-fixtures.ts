/** Analytics contract fixtures: the Phase 10 definition-of-done question, answered. */

import type { AnalyticsAnswer, AnalyticsModelAvailable, QueryResult } from "@/lib/analytics/api";

export const monthlyRevenue: QueryResult = {
  id: "Q1",
  purpose: "Monthly revenue in 2026 by product line",
  sql: "SELECT date_trunc('month', invoice_date) AS month, product_line, sum(amount_eur) AS revenue_eur\nFROM invoice_lines JOIN products USING (product_id)\nWHERE invoice_date >= DATE '2026-01-01'\nGROUP BY 1, 2 ORDER BY 1, 2",
  columns: [
    { name: "month", type: "date", format: "date" },
    { name: "product_line", type: "text", format: "text" },
    { name: "revenue_eur", type: "decimal", format: "eur" },
  ],
  rows: [
    ["2026-02-01", "AMR-200", 410000],
    ["2026-01-01", "AMR-200", 380000],
    ["2026-01-01", "AMR-500", 1150000],
    ["2026-02-01", "AMR-500", 1210000],
    ["2026-03-01", "AMR-200", 395000],
    ["2026-03-01", "AMR-500", 1320000],
  ],
  row_count: 6,
  truncated: false,
  elapsed_ms: 14,
  error: null,
};

export const topDrivers: QueryResult = {
  id: "Q2",
  purpose: "Top 3 customers by revenue growth, Q2 to Q3",
  sql: "SELECT customers.name, sum(amount_eur) AS growth_eur FROM invoice_lines JOIN customers USING (customer_id) GROUP BY 1 ORDER BY 2 DESC LIMIT 3",
  columns: [
    { name: "name", type: "text", format: "text" },
    { name: "growth_eur", type: "decimal", format: "eur" },
  ],
  rows: [
    ["Bluefin Freight", 1650000],
    ["Polaris Parcel", 1320000],
    ["Nordlicht \"Logistik\", GmbH", 410000.5],
  ],
  row_count: 3,
  truncated: false,
  elapsed_ms: 9,
  error: null,
};

export function answer(overrides: Partial<AnalyticsAnswer> = {}): AnalyticsAnswer {
  return {
    id: "0190f2c4-aaaa-7000-8000-000000000001",
    question: "Monthly revenue trend this year and the top 3 drivers",
    status: "answered",
    error: null,
    narrative:
      "Revenue grew from **€1.53M** in January to €1.72M in March [Q1]. The top driver was Bluefin Freight with €1.65M [Q2], ahead of Polaris Parcel (€1.32M) [Q2]. Growth was about 12% [Q1, Q2].",
    chart: {
      type: "stacked_bar",
      query: "Q1",
      x: "month",
      y: ["revenue_eur"],
      series: "product_line",
      title: "Monthly revenue by product line, 2026",
      y_format: "eur",
    },
    queries: [monthlyRevenue, topDrivers],
    main_query: "Q1",
    verification: {
      numbers: [
        { text: "€1.53M", value: 1530000, verified: true, query: "Q1" },
        { text: "€1.72M", value: 1715000, verified: true, query: "Q1" },
        { text: "€1.65M", value: 1650000, verified: true, query: "Q2" },
        { text: "€1.32M", value: 1320000, verified: true, query: "Q2" },
        { text: "12%", value: 12, verified: false, query: null },
      ],
      verified: 4,
      total: 5,
    },
    sources: [
      { table: "invoice_lines", description: "One row per invoice line, in euros." },
      { table: "products", description: "The product catalogue." },
    ],
    usage: { model: "claude-sonnet-4-5", input_tokens: 5400, output_tokens: 820, cost_usd: 0.0285 },
    created_at: "2026-11-12T09:30:00Z",
    ...overrides,
  };
}

export const model: AnalyticsModelAvailable = {
  available: true,
  name: "Acme sales",
  description: "Invoices, customers, products and the sales pipeline.",
  data_through: "2026-09-30",
  metrics: [
    { name: "revenue", description: "Invoiced revenue, excluding void lines", format: "eur" },
    { name: "win_rate", description: "Won deals as a share of closed deals", format: "percent" },
  ],
  dimensions: [
    { name: "region", description: "Customer region" },
    { name: "month", description: "Calendar month" },
    { name: "quarter", description: "Calendar quarter" },
    { name: "year", description: "Calendar year" },
  ],
  tables: [
    {
      name: "invoice_lines",
      description: "One row per invoice line",
      columns: [
        { name: "invoice_date", type: "date", description: "" },
        { name: "amount_eur", type: "decimal", description: "Net amount" },
      ],
    },
  ],
  row_policy: "You see only the customers and deals you own.",
  examples: ["Monthly revenue trend this year and the top 3 drivers", "Win rate by region last quarter"],
};
