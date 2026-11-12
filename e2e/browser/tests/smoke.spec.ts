import { expect, test } from "./support/fixtures";
import { ADMIN_PAGES, expectRendered, openPage, settled, WORKSPACE_PAGES } from "./support/pages";
import { ALL_USERS, seesAdministration, USERS } from "./support/users";

// Every page, as every role: it renders its heading, finishes loading, shows no error, and
// the page guard (support/fixtures.ts) sees no console error and no failed request.
test.describe.configure({ mode: "parallel" });

/** An error the page shows instead of its content (ErrorAlert: a destructive alert). */
const errorAlerts = '[data-slot="alert"][data-variant="destructive"]:visible';

for (const user of ALL_USERS) {
  test.describe(`as ${user.key} (${user.roleLabel})`, () => {
    test.use({ user });

    for (const target of WORKSPACE_PAGES) {
      test(`${target.path} renders`, async ({ page }) => {
        await openPage(page, target);
        await expect(page.locator(errorAlerts)).toHaveCount(0);
      });
    }

    if (seesAdministration(user)) {
      for (const target of ADMIN_PAGES) {
        test(`${target.path} renders`, async ({ page }) => {
          await openPage(page, target);
          await expect(page.locator(errorAlerts)).toHaveCount(0);
          await expect(page.getByRole("navigation", { name: "Administration sections" }).getByRole("link", { name: /./ }).first()).toBeVisible();
        });
      }
    } else {
      test("the admin pages refuse, without showing any admin data", async ({ page }) => {
        // The page's own requests may start before the session says who this is; the API
        // answers them 403 (allowed by the guard) and nothing of them is shown.
        for (const target of [{ path: "/admin" }, ...ADMIN_PAGES]) {
          await page.goto(target.path);
          await expect(page.getByText("You don't have access to administration")).toBeVisible();
          await expect(page.getByRole("navigation", { name: "Administration sections" })).toHaveCount(0);
          await expect(page.getByRole("heading", { level: 3 })).toHaveCount(0);
        }
      });
    }
  });
}

test.describe("detail pages, reached by clicking through", () => {
  test.use({ user: USERS.erin });

  test("a collection, one of its documents and its chunks", async ({ page }) => {
    await openPage(page, WORKSPACE_PAGES.find((p) => p.path === "/knowledge")!);
    const card = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: "Company Handbook", level: 3 }) });
    await card.getByRole("link", { name: "Company Handbook" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "Company Handbook" })).toBeVisible();
    await settled(page);
    const firstDocument = page.getByRole("table").getByRole("link").first();
    const title = (await firstDocument.textContent())!.trim();
    await firstDocument.click();
    await expect(page.getByRole("heading", { level: 2, name: title })).toBeVisible();
    await settled(page);
    await expect(page.locator(errorAlerts)).toHaveCount(0);
  });

  test("a department and a group in Admin", async ({ page }) => {
    for (const [path, name] of [
      ["/admin/departments", "Engineering"],
      ["/admin/groups", "Leadership"],
    ]) {
      await openPage(page, ADMIN_PAGES.find((p) => p.path === path)!);
      await page.getByRole("link", { name, exact: true }).first().click();
      await expect(page).toHaveURL(new RegExp(`${path}/[0-9a-f-]{36}$`));
      await expect(page.getByRole("heading", { name, exact: true }).first()).toBeVisible();
      await settled(page);
      await expect(page.locator(errorAlerts)).toHaveCount(0);
    }
  });

  test("a meeting, when there is one", async ({ page }) => {
    await openPage(page, WORKSPACE_PAGES.find((p) => p.path === "/meetings")!);
    const meetings = page.getByRole("region", { name: "Recent meetings" }).getByRole("link");
    // CI seeds no meetings (they need a model); the empty state is then the page's content.
    if ((await meetings.count()) === 0) {
      await expect(page.getByText("No meetings yet")).toBeVisible();
      return;
    }
    const title = (await meetings.first().textContent())!.trim();
    await meetings.first().click();
    await expect(page.getByRole("heading", { level: 2 }).filter({ hasText: title.slice(0, 20) }).first()).toBeVisible();
    await settled(page);
    await expect(page.locator(errorAlerts)).toHaveCount(0);
  });
});

test("signed out, every workspace page asks you to sign in instead of failing", async ({ page }) => {
  for (const target of WORKSPACE_PAGES) {
    await page.goto(target.path);
    await expectRendered(page, { ...target, heading: { name: /./, level: 1 } });
    await expect(page.getByRole("link", { name: "Sign in" }).first()).toBeVisible();
    await expect(page.locator(errorAlerts)).toHaveCount(0);
  }
});
