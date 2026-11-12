import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Cell, ResultColumn } from "@/lib/analytics/api";

import { ResultTable } from "./result-table";

describe("ResultTable", () => {
  it("shows list, map and struct columns as text instead of crashing", () => {
    const columns: ResultColumn[] = [
      { name: "product_line", type: "VARCHAR", format: "text" },
      { name: "customers", type: "VARCHAR[]", format: "text" },
      { name: "statuses", type: "MAP(VARCHAR, UBIGINT)", format: "text" },
      { name: "s", type: "STRUCT(top BIGINT)", format: "text" },
    ];
    const rows: Cell[][] = [["AMR-200", ["C001", "C002", "C003"], { open: 1, paid: 139, void: 3 }, { top: 213238 }]];

    const html = renderToStaticMarkup(<ResultTable columns={columns} rows={rows} caption="Q1" />);

    expect(html).toContain("C001, C002, C003");
    expect(html).toContain("open: 1, paid: 139, void: 3");
    expect(html).toContain("top: 213238");
  });
});
