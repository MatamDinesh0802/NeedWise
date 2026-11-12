import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";

import { modelColumnType, modelProblems, scaffoldModel, suggestTableName, tableNameError } from "./admin";

const invalid = (detail: string, extra: Record<string, unknown> = {}) =>
  new ApiError({
    status: 422,
    title: "Invalid data model",
    code: "invalid_model",
    detail,
    problem: { type: "about:blank", title: "Invalid data model", status: 422, code: "invalid_model", detail, ...extra },
  });

describe("table names", () => {
  it.each([
    ["customers", null],
    ["invoice_lines_2026", null],
    ["", "Add a table name."],
    ["Customers", "lower-case"],
    ["2026_sales", "lower-case"],
    ["invoice-lines", "lower-case"],
    ["a".repeat(64), "At most 63"],
  ])("%j", (name, problem) => {
    const error = tableNameError(name);
    if (problem === null) expect(error).toBeNull();
    else expect(error).toContain(problem);
  });

  it("suggests a name from the file name", () => {
    expect(suggestTableName("Invoice Lines 2026.xlsx")).toBe("invoice_lines_2026");
    expect(suggestTableName("customers.csv")).toBe("customers");
    expect(suggestTableName("2026 Q3 — Übersicht.csv")).toBe("t_2026_q3_ubersicht");
    expect(suggestTableName("---.csv")).toBe("");
    expect(tableNameError(suggestTableName(`${"x".repeat(80)}.csv`))).toBeNull();
  });
});

describe("modelProblems", () => {
  it("reads one problem per line, with or without bullets and a heading", () => {
    expect(modelProblems(invalid("The model has 2 problems:\n- metric revenue: unknown table invoices\n- access: add at least one principal"))).toEqual([
      "metric revenue: unknown table invoices",
      "access: add at least one principal",
    ]);
    expect(modelProblems(invalid("1. not valid YAML: line 3\n2) tables: required"))).toEqual(["not valid YAML: line 3", "tables: required"]);
  });

  it("keeps a YAML syntax error as one problem, caret and all", () => {
    const detail = 'not valid YAML: mapping values are not allowed here\n  in "<unicode string>", line 2, column 6:\n      bad: : :\n         ^';
    expect(modelProblems(invalid(detail))).toEqual(["not valid YAML: mapping values are not allowed here\n  line 2, column 6:\n      bad: : :\n         ^"]);
  });

  it("splits a one-line list on semicolons", () => {
    expect(modelProblems(invalid("join: unknown column x.y; metric m: unknown table t"))).toEqual(["join: unknown column x.y", "metric m: unknown table t"]);
  });

  it("reads Pydantic's validation text", () => {
    const detail = "2 validation errors for SemanticModel\naccess\n  List should have at least 1 item [type=too_short]\nmetrics\n  Field required [type=missing]";
    expect(modelProblems(invalid(detail))).toEqual(["access: List should have at least 1 item", "metrics: Field required"]);
  });

  it("prefers a problems array, and ignores other errors", () => {
    expect(modelProblems(invalid("2 problems", { problems: ["a", { message: "b" }] }))).toEqual(["a", "b"]);
    expect(modelProblems(new ApiError({ status: 422, title: "x", code: "validation_error", detail: "y" }))).toEqual([]);
    expect(modelProblems(new Error("boom"))).toEqual([]);
    expect(modelProblems(invalid(""))).toEqual(["The data model isn't valid."]);
  });
});

describe("scaffoldModel", () => {
  it("maps dataset types", () => {
    expect(["VARCHAR", "BIGINT", "DECIMAL(18,2)", "DOUBLE", "DATE", "TIMESTAMP", "BOOLEAN"].map(modelColumnType)).toEqual([
      "text",
      "integer",
      "decimal",
      "decimal",
      "date",
      "date",
      "boolean",
    ]);
  });

  it("lists every table and column, with the first date as time column", () => {
    const yaml = scaffoldModel([
      {
        name: "invoice_lines",
        rows: 478,
        columns: [
          { name: "invoice_line_id", type: "VARCHAR" },
          { name: "invoice_date", type: "DATE" },
          { name: "amount_eur", type: "DECIMAL(18,2)" },
        ],
      },
    ]);
    expect(yaml).toContain("access:\n  - org");
    expect(yaml).toContain("  invoice_lines:\n    description: \"TODO: what one row of invoice_lines is\"\n    time_column: invoice_date\n    columns:");
    expect(yaml).toContain("      amount_eur:\n        type: decimal");
    expect(yaml).toContain("  invoice_lines_count:\n    table: invoice_lines\n    sql: count(*)");
    expect(yaml.endsWith("row_policies: []\n")).toBe(true);
  });
});
