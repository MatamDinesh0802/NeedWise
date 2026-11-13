import { describe, expect, it } from "vitest";

import type { CatalogTable, SqlSource } from "./api";
import {
  emptySourceForm,
  parseSchemas,
  scaffoldSourceModel,
  sourceFormErrors,
  sourceStatus,
  suggestSourceKey,
  toSourceInput,
  type SourceForm,
} from "./sources";

const filled: SourceForm = {
  ...emptySourceForm("postgres"),
  name: "Sales warehouse",
  key: "sales-warehouse",
  host: "db.internal.example",
  database: "sales",
  username: "needwise_reader",
  password: "s3cret",
  schemas: "shop, finance",
};

describe("the connection form", () => {
  it("accepts a complete form and builds what the API takes", () => {
    expect(sourceFormErrors(filled)).toEqual({});
    expect(toSourceInput(filled)).toEqual({
      key: "sales-warehouse",
      name: "Sales warehouse",
      kind: "postgres",
      host: "db.internal.example",
      database: "sales",
      username: "needwise_reader",
      password: "s3cret",
      tls_mode: "verify-full",
      tls_ca: null,
      schemas: ["shop", "finance"],
    });
    expect(toSourceInput({ ...filled, port: "6543" }).port).toBe(6543);
  });

  it.each([
    [{ name: " " }, "name"],
    [{ key: "Sales DB" }, "key"],
    [{ key: "uploads" }, "key"],
    [{ host: "" }, "host"],
    [{ host: "postgres://db" }, "host"],
    [{ host: "db/x" }, "host"],
    [{ host: "user@db" }, "host"],
    [{ port: "70000" }, "port"],
    [{ port: "54x" }, "port"],
    [{ database: "" }, "database"],
    [{ username: "" }, "username"],
    [{ password: "" }, "password"],
    [{ schemas: "" }, "schemas"],
    [{ schemas: "shop; DROP" }, "schemas"],
    [{ tls_ca: "not a certificate" }, "tls_ca"],
  ] as const)("refuses %j", (change, field) => {
    expect(Object.keys(sourceFormErrors({ ...filled, ...change }))).toEqual([field]);
  });

  it("splits schemas on commas and spaces, once each", () => {
    expect(parseSchemas(" shop,finance  shop\npublic ")).toEqual(["shop", "finance", "public"]);
  });

  it("suggests a key from the name, never the uploads key", () => {
    expect(suggestSourceKey("Sales Warehouse (EU)")).toBe("sales-warehouse-eu");
    expect(suggestSourceKey("Uploads")).toBe("uploads-db");
    expect(emptySourceForm("mysql").schemas).toBe("");
    expect(emptySourceForm().schemas).toBe("public");
  });
});

describe("sourceStatus", () => {
  const base = { enabled: true, yaml: "name: x", checked_at: "2026-11-13T10:00:00Z", server_version: "PostgreSQL 18.1 on aarch64", last_error: null };
  it("says whether the last connection worked, and why not", () => {
    expect(sourceStatus(base)).toEqual({ tone: "ok", text: "Connected: PostgreSQL 18.1." });
    expect(sourceStatus({ ...base, yaml: null }).text).toContain("No data model yet");
    expect(sourceStatus({ ...base, last_error: "Could not connect to db:5432." })).toEqual({ tone: "error", text: "Could not connect to db:5432." });
    expect(sourceStatus({ ...base, checked_at: null, server_version: null }).tone).toBe("unknown");
    expect(sourceStatus({ ...base, enabled: false }).text).toContain("Turned off");
  });
});

describe("scaffoldSourceModel", () => {
  const tables: CatalogTable[] = [
    {
      schema: "shop",
      name: "orders",
      kind: "table",
      columns: [
        { name: "order_id", type: "character varying", model_type: "text", usable: true },
        { name: "order_date", type: "date", model_type: "date", usable: true },
        { name: "amount_eur", type: "numeric", model_type: "decimal", usable: true },
        { name: "OrderNote", type: "text", model_type: "text", usable: false },
      ],
    },
    { schema: "finance", name: "orders", kind: "view", columns: [{ name: "n", type: "bigint", model_type: "integer", usable: true }] },
  ];
  const source = { name: "Shop", kind: "postgres" } as Pick<SqlSource, "name" | "kind">;

  it("writes each picked table with its source table, usable columns and time column", () => {
    const yaml = scaffoldSourceModel(source, tables);
    expect(yaml).toContain("  orders:\n    source_table: shop.orders\n");
    expect(yaml).toContain("    time_column: order_date\n");
    expect(yaml).toContain("      amount_eur:\n        type: decimal\n");
    expect(yaml).not.toContain("      OrderNote:");
    expect(yaml).toContain("# left out (not lower_snake_case; rename them in a view): OrderNote");
    // Two tables named orders: the second gets its schema in front.
    expect(yaml).toContain("  finance_orders:\n    source_table: finance.orders\n");
    expect(yaml).toContain("  orders_count:\n    table: orders\n    sql: count(*)");
    expect(yaml).toContain("PostgreSQL SQL");
    expect(yaml).toContain('name: "Shop"');
    expect(yaml.endsWith("row_policies: []\n")).toBe(true);
  });
});
