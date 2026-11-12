import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { EMPTY_AUDIT_FILTERS, type AuditFilterState } from "@/lib/admin/audit";

import { AuditFilterForm, ExportCard } from "./audit-section";

const erin = { id: "u-erin", display_name: "Erin Takahashi" };
const filters = (overrides: Partial<AuditFilterState> = {}): AuditFilterState => ({ ...EMPTY_AUDIT_FILTERS, ...overrides });

describe("AuditFilterForm", () => {
  it("is a labelled search form with every filter the API has", () => {
    const html = renderToStaticMarkup(<AuditFilterForm initial={EMPTY_AUDIT_FILTERS} onApply={() => {}} />);
    expect(html).toContain('role="search" aria-label="Filter the audit log"');
    for (const label of ["Text", "Action starts with", "Resource type", "Resource ID", "From", "To (inclusive)"]) {
      expect(html).toMatch(new RegExp(`<label[^>]*for="([^"]+)"[^>]*>${label.replace(/[()]/g, "\\$&")}</label>.*?id="\\1"`));
    }
    expect(html).toContain("Filter by the person who acted");
    expect(html).toContain("Showing every event.");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Clear all<\/button>/);
  });

  it("shows the chosen person with a way to remove them, and how many filters apply", () => {
    const html = renderToStaticMarkup(<AuditFilterForm initial={filters({ actor: erin, action: "knowledge." })} onApply={() => {}} />);
    expect(html).toContain("Erin Takahashi");
    expect(html).toContain('aria-label="Stop filtering by Erin Takahashi"');
    expect(html).toContain("2 filters applied.");
    expect(html).toContain('value="knowledge."');
  });
});

describe("ExportCard", () => {
  it("exports the filtered view as CSV, and keeps the SIEM export behind the license check", () => {
    const html = renderToStaticMarkup(<ExportCard filters={filters({ action: "auth.", actor: erin })} />);
    expect(html).toContain('href="/api/v1/admin/audit-events/export.csv?action=auth.&amp;actor_id=u-erin"');
    expect(html).toContain("Download this view as CSV");
    expect(html).toContain("The newest 10,000 events that match the filters");
    expect(html).toContain("audit.export");
    expect(html).toContain("For a SIEM");
    // The edition loads on the client: until then the gate shows nothing of the export.
    expect(html).toContain('aria-label="Checking the license"');
    expect(html).not.toContain("export.jsonl");
  });

  it("exports everything without filters", () => {
    const html = renderToStaticMarkup(<ExportCard filters={EMPTY_AUDIT_FILTERS} />);
    expect(html).toContain('href="/api/v1/admin/audit-events/export.csv"');
    expect(html).toContain("Download as CSV");
  });
});
