import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { EditionInfo } from "@/lib/edition";
import { newRow } from "@/lib/usage/budgets";
import { adminBudgets } from "@/test/usage-fixtures";

import { BudgetsEditor, DefaultCapFields, RowFields } from "./budgets-section";

const enterprise: EditionInfo = { edition: "enterprise", features: ["department_budgets"], licensee: "Acme", expires_at: "2027-01-01T00:00:00Z" };
const community: EditionInfo = { edition: "community", features: [], licensee: null, expires_at: null };

describe("BudgetsEditor", () => {
  it("shows every saved budget as a named group of labelled fields, with what was spent", () => {
    const html = renderToStaticMarkup(<BudgetsEditor saved={adminBudgets} edition={enterprise} />);
    expect(html).toContain("<legend class=\"sr-only\">Monthly organization budget</legend>");
    expect(html).toContain("<legend class=\"sr-only\">Alice Anders</legend>");
    expect(html).toContain("<legend class=\"sr-only\">Finance</legend>");
    expect(html).toMatch(/<label[^>]*for="([^"]+)"[^>]*>Limit \(USD\)<\/label>.*?<input id="\1"[^>]*value="500"/);
    expect(html).toContain('aria-label="Remove Monthly organization budget"');
    expect(html).toContain("$212.40 spent this month");
    expect(html).toContain("Within budget: warns at 80%");
    expect(html).toContain("Limit reached: 107% used");
  });

  it("starts saved: nothing to save or discard", () => {
    const html = renderToStaticMarkup(<BudgetsEditor saved={adminBudgets} edition={enterprise} />);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Save budgets<\/button>/);
    expect(html).toContain("Everything is saved.");
  });

  it("offers people from the directory and departments with an Enterprise license", () => {
    const html = renderToStaticMarkup(<BudgetsEditor saved={adminBudgets} edition={enterprise} />);
    expect(html).toContain("Add a cap for a person");
    expect(html).toContain("Department budgets");
    expect(html).not.toContain("Enterprise feature: Department budgets");
  });

  it("explains department budgets in Community, and that saving removes saved ones", () => {
    const html = renderToStaticMarkup(<BudgetsEditor saved={adminBudgets} edition={community} />);
    expect(html).toContain("Enterprise feature: Department budgets");
    expect(html).not.toContain("<legend class=\"sr-only\">Finance</legend>");
    expect(html).toContain("1 saved department budget");
    expect(html).toContain("saving this page removes it");
  });

  it("keeps department budgets while the edition is unknown", () => {
    const html = renderToStaticMarkup(<BudgetsEditor saved={adminBudgets} edition={null} editionLoading />);
    expect(html).toContain('aria-label="Checking the license"');
    expect(html).not.toContain("saving this page removes");
  });

  it("says when there is no organization budget", () => {
    const html = renderToStaticMarkup(<BudgetsEditor saved={{ budgets: [], user_default: null }} edition={community} />);
    expect(html).toContain("No organization budget: usage as a whole has no limit.");
    expect(html).toContain("Add an organization budget");
    expect(html).not.toContain("saved department budget");
  });
});

describe("RowFields", () => {
  it("marks invalid fields and ties each message to its field", () => {
    const row = { ...newRow("user", null), limit: "0" };
    const html = renderToStaticMarkup(
      <RowFields
        row={row}
        title="Choose a person"
        errors={{ [`${row.key}.limit`]: "Enter a limit in US dollars, more than 0.", [`${row.key}.subject`]: "Choose a person." }}
        onChange={() => {}}
        onRemove={() => {}}
      />,
    );
    expect(html).toMatch(/aria-describedby="([^"]+-error)" aria-invalid="true".*<p id="\1"[^>]*>Enter a limit in US dollars, more than 0\./);
    expect(html).toContain(">Choose a person.</p>");
    expect(html).toContain("Not saved yet.");
    expect(html).toContain("When it is used up, requests that use a model are refused until it resets.");
  });
});

describe("DefaultCapFields", () => {
  it("hides the fields while the default cap is off, and says what that means", () => {
    const off = renderToStaticMarkup(<DefaultCapFields cap={{ enabled: false, period: "month", limit: "", action: "block" }} error={null} onChange={() => {}} />);
    expect(off).toContain("No default cap");
    expect(off).not.toContain("Limit per person (USD)");
    const on = renderToStaticMarkup(<DefaultCapFields cap={{ enabled: true, period: "day", limit: "5", action: "warn" }} error={null} onChange={() => {}} />);
    expect(on).toContain("Limit per person (USD)");
    expect(on).toContain('value="5"');
    expect(on).toContain("Requests keep working; people are only told.");
  });
});
