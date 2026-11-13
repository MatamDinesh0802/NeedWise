import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { SqlSource } from "@/lib/analytics/api";

import { AddSourceForm, SourceCard, SourceModelEditor, TablePicker } from "./analytics-sources";

const shop: SqlSource = {
  key: "shop",
  name: "Shop orders",
  kind: "postgres",
  host: "db.internal.example",
  port: 5432,
  database: "shop",
  username: "workmind_reader",
  password_set: true,
  tls_mode: "verify-full",
  tls_ca: null,
  schemas: ["shop"],
  enabled: true,
  yaml: "name: Shop orders\n",
  checked_at: "2026-11-13T10:00:00Z",
  server_version: "PostgreSQL 18.1 on aarch64-unknown-linux-gnu",
  last_error: null,
  created_at: "2026-11-13T09:00:00Z",
  updated_at: "2026-11-13T10:00:00Z",
};

describe("AddSourceForm", () => {
  it("asks for the connection with labelled fields, a password field and the TLS mode", () => {
    const html = renderToStaticMarkup(<AddSourceForm onDone={() => {}} />);
    for (const label of ["Database type", "Name", "Key", "Host", "Port", "Database", "User", "Password", "Schemas to read tables from", "TLS"]) {
      expect(html).toMatch(new RegExp(`<label[^>]*>${label}</label>`));
    }
    expect(html).toContain('type="password"');
    expect(html).toContain('autoComplete="new-password"');
    expect(html).toContain("A user that may only read (SELECT).");
    expect(html).toContain("CA certificate (optional)"); // verify-full is the default
    expect(html).toContain("Connect</button>");
  });
});

describe("SourceCard", () => {
  it("shows where it connects, its status and its actions, never a password", () => {
    const html = renderToStaticMarkup(<SourceCard source={shop} />);
    expect(html).toContain("Shop orders");
    expect(html).toContain("workmind_reader@db.internal.example:5432/shop · shop · TLS verify-full");
    expect(html).toContain("Connected: PostgreSQL 18.1.");
    expect(html).toContain("Test connection");
    expect(html).toContain("Turn off");
    expect(html).toContain('aria-label="Remove Shop orders"');
    expect(html).toContain("Replace the password");
    expect(html).toContain("Show its tables");
  });

  it("says why the last connection failed", () => {
    const html = renderToStaticMarkup(<SourceCard source={{ ...shop, last_error: "Could not connect to db.internal.example:5432 (Connection refused)." }} />);
    expect(html).toContain("text-destructive");
    expect(html).toContain("Could not connect to db.internal.example:5432");
  });
});

describe("TablePicker and SourceModelEditor", () => {
  it("offers to load the catalog, and edits the model in a labelled box", () => {
    expect(renderToStaticMarkup(<TablePicker source={shop} onScaffold={() => {}} />)).toContain("Show its tables");
    const html = renderToStaticMarkup(<SourceModelEditor source={shop} draft={null} />);
    expect(html).toMatch(/<label[^>]*for="([^"]+)"[^>]*>Data model of Shop orders \(YAML, PostgreSQL SQL\)<\/label><textarea[^>]*id="\1"/);
    expect(html).toContain("source_table: schema.table");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>.*Save model<\/button>/); // nothing changed yet
    const drafted = renderToStaticMarkup(<SourceModelEditor source={{ ...shop, yaml: null }} draft={"name: Draft\n"} />);
    expect(drafted).toContain("name: Draft");
    expect(drafted).not.toMatch(/<button[^>]*disabled=""[^>]*>.*Save model<\/button>/);
  });
});
