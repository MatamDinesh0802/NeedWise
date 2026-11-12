import { expect, sameOrigin, test } from "./support/fixtures";
import { E2E_PREFIX, USERS } from "./support/users";

// These need a working chat model (and, for the citation, real embeddings help), so they are
// tagged @model and run only with E2E_MODEL=1 (playwright.config.ts). CI has no model key.
// Each makes a few paid model calls; they count toward the organization's usage.

const MODEL_TIMEOUT = 120_000;

test.describe("with a model", { tag: "@model" }, () => {
  test.describe.configure({ timeout: 4 * 60_000 });

  test.describe("chat", () => {
    test.use({ user: USERS.bob });

    test("bob asks about parental leave and gets an answer that cites a document he may see", async ({ page, baseURL }) => {
      let conversationId: string | null = null;
      try {
        await page.goto("/chat");
        await page.getByLabel("Ask a question").fill("How many weeks of parental leave do we get?");
        await page.getByRole("button", { name: "Send" }).click();
        await expect(page).toHaveURL(/\/chat\/[0-9a-f-]{36}$/);
        conversationId = page.url().split("/").pop()!;

        // A citation chip ("Source 1: <title>") appears in the answer once it has streamed.
        const chip = page.getByRole("button", { name: /^Source \d+: / }).first();
        await expect(chip).toBeVisible({ timeout: MODEL_TIMEOUT });
        await expect(page.getByText(/16 weeks/).first()).toBeVisible();
        const title = (await chip.getAttribute("aria-label"))!.replace(/^Source \d+: /, "");

        // The chip opens the sources panel at that document.
        await chip.click();
        const sources = page.getByRole("complementary", { name: "Sources" });
        await expect(sources).toBeVisible();
        await expect(sources.getByText(title).first()).toBeVisible();
      } finally {
        if (conversationId) {
          const deleted = await page.request.delete(`/api/v1/conversations/${conversationId}`, { headers: sameOrigin(baseURL!) });
          expect(deleted.status(), "the test's conversation is deleted").toBe(204);
        }
      }
    });
  });

  test.describe("insights", () => {
    test.use({ user: USERS.hannah });

    test("hannah asks for revenue by month and gets a chart with a table view", async ({ page }) => {
      // Analytics runs can't be deleted through the API (retention removes them); the prefix
      // marks this one as test data in the run history.
      await page.goto("/insights");
      await page.getByLabel("Ask a question about the data").fill(`${E2E_PREFIX} check: monthly revenue in 2026`);
      await page.getByRole("button", { name: "Ask" }).click();
      const chart = page.locator('[aria-roledescription="chart"]').first();
      await expect(chart).toBeVisible({ timeout: MODEL_TIMEOUT });
      await page.getByRole("group", { name: "Show as" }).getByRole("button", { name: "Table" }).click();
      await expect(page.getByRole("table").first()).toBeVisible();
    });
  });

  test.describe("agent test console", () => {
    test.use({ user: USERS.erin });

    test("erin tests the HR agent: the planner picks it and it answers with citations, rendered as markdown", async ({ page }) => {
      // The console stores nothing (no conversation); only usage is recorded.
      await page.goto("/admin/agents");
      await page.getByRole("button", { name: "Edit HR agent" }).click();
      const dialog = page.getByRole("dialog", { name: "Edit HR agent" });
      await dialog.getByRole("tab", { name: "Test console" }).click();
      await dialog.getByRole("textbox", { name: "Question", exact: true }).fill("How many weeks of parental leave do we get?");
      await dialog.getByRole("button", { name: "Run test" }).click();

      const routing = dialog.getByRole("region", { name: "Routing" });
      await expect(routing).toBeVisible({ timeout: MODEL_TIMEOUT });
      await expect(routing.locator("strong")).toContainText("HR agent");
      const answer = dialog.getByRole("region", { name: "Answer from this agent" });
      await expect(answer).toBeVisible();
      await expect(answer.getByRole("button", { name: /^Source \d+: / }).first()).toBeVisible();
      await expect(answer).not.toContainText("**");
      await expect(answer.getByRole("heading", { name: "Citations" })).toBeVisible();

      await dialog.getByRole("button", { name: "Cancel" }).click();
      await expect(dialog).toBeHidden();
    });
  });
});

