import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { expect, test } from "./support/fixtures";
import { MAIN_ADMIN_PATHS, MAIN_PATHS, settled } from "./support/pages";
import { USERS } from "./support/users";

// axe-core (WCAG 2.2 A/AA and best practices) on the main pages, signed in, in the light theme
// and on three pages in the dark theme. Serious and critical violations fail the test; the full
// list (moderate and minor too) is attached to the report.
test.describe.configure({ mode: "parallel" });

const FAILING_IMPACTS = new Set(["serious", "critical"]);

/**
 * Findings judged not to be barriers, each limited to one rule on one element. Keep this short
 * and say why; anything else axe finds is fixed in the app.
 */
const EXCLUSIONS: { rule: string; selector: string; why: string }[] = [
  {
    // The button's name is "Account menu for <name>" and its visible text is the avatar's
    // initials followed by the name. axe counts the initials as label text, so the name never
    // "contains" the visible text. The initials are an avatar, not a label: speech input users
    // say the name, which is in the accessible name (WCAG 2.5.3 Label in Name is met).
    rule: "label-content-name-mismatch",
    selector: 'button[aria-label^="Account menu for "]',
    why: "avatar initials are not a label",
  },
];

async function audit(page: Page, label: string, include?: string): Promise<void> {
  let builder = new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]);
  if (include) builder = builder.include(include);
  const results = await builder.analyze();
  await test.info().attach(`axe ${label}`, { body: JSON.stringify(results.violations, null, 2), contentType: "application/json" });
  const failing: string[] = [];
  for (const violation of results.violations.filter((v) => FAILING_IMPACTS.has(v.impact ?? ""))) {
    const exclusions = EXCLUSIONS.filter((e) => e.rule === violation.id);
    const nodes: string[] = [];
    for (const node of violation.nodes) {
      const target = node.target.join(" ");
      const excluded =
        exclusions.length > 0 &&
        (await page.locator(target).evaluate((el, selectors) => selectors.some((s) => el.matches(s)), exclusions.map((e) => e.selector)));
      if (!excluded) nodes.push(target);
    }
    if (nodes.length > 0) failing.push(`${violation.impact} ${violation.id}: ${violation.help} (${nodes.length}×: ${nodes.slice(0, 3).join(" | ")})`);
  }
  expect(failing, `serious or critical accessibility violations on ${label}`).toEqual([]);
}

async function openAndSettle(page: Page, path: string): Promise<void> {
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await settled(page);
}

test.describe("as bob (member)", () => {
  test.use({ user: USERS.bob, colorScheme: "light" });
  for (const path of MAIN_PATHS) {
    test(`${path} has no serious accessibility violations`, async ({ page }) => {
      await openAndSettle(page, path);
      await audit(page, path);
    });
  }
});

test.describe("as erin (admin)", () => {
  test.use({ user: USERS.erin, colorScheme: "light" });
  for (const path of MAIN_ADMIN_PATHS) {
    test(`${path} has no serious accessibility violations`, async ({ page }) => {
      await openAndSettle(page, path);
      await audit(page, path);
    });
  }

  test("the agent editor and its test console have no serious accessibility violations", async ({ page }) => {
    await openAndSettle(page, "/admin/agents");
    await page.getByRole("button", { name: "Edit HR agent" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("tab", { name: "Definition" })).toBeVisible();
    await audit(page, "agent editor: definition", '[role="dialog"]');
    await dialog.getByRole("tab", { name: "Test console" }).click();
    await expect(dialog.getByRole("textbox", { name: "Question", exact: true })).toBeVisible();
    await audit(page, "agent editor: test console", '[role="dialog"]');
  });
});

test.describe("dark theme", () => {
  test.use({ user: USERS.bob, colorScheme: "dark" });
  for (const path of ["/", "/knowledge", "/chat"]) {
    test(`${path} has no serious accessibility violations in the dark theme`, async ({ page }) => {
      await openAndSettle(page, path);
      await expect(page.locator("html")).toHaveClass(/dark/);
      await audit(page, `${path} (dark)`);
    });
  }
});

test("signed out, the overview has no serious accessibility violations", async ({ page }) => {
  await openAndSettle(page, "/");
  await audit(page, "/ (signed out)");
});
