import { editionOf, expectGate } from "./support/edition";
import { expect, test } from "./support/fixtures";
import { openPage, settled, WORKSPACE_PAGES } from "./support/pages";
import { USERS } from "./support/users";

// Approvals, tasks, meetings and usage. Their contents depend on what people did on the stack
// (CI starts empty), so each tab shows either its items or its empty state: never an error.

const workspace = (path: string) => WORKSPACE_PAGES.find((p) => p.path === path)!;
const errorAlerts = '[data-slot="alert"][data-variant="destructive"]:visible';

test.describe("as bob", () => {
  test.use({ user: USERS.bob });

  test("the approvals inbox: three tabs, each with its approvals or its empty state", async ({ page }) => {
    await openPage(page, workspace("/approvals"));
    const tabs: [string, string][] = [
      ["Waiting for you", "Nothing is waiting for you"],
      ["Requested by you", "You haven't asked for anything yet"],
      ["Decided by you", "No decisions yet"],
    ];
    for (const [tab, empty] of tabs) {
      await page.getByRole("tab", { name: new RegExp(`^${tab}`) }).click();
      await expect(page.getByRole("tab", { name: new RegExp(`^${tab}`) })).toHaveAttribute("aria-selected", "true");
      const panel = page.getByRole("tabpanel");
      await expect(panel.getByText(empty).or(panel.getByRole("article").first()).or(panel.getByRole("listitem").first())).toBeVisible();
    }
    await expect(page.locator(errorAlerts)).toHaveCount(0);
  });

  test("tasks: mine, my team queues and created by me; a new task can be started and cancelled", async ({ page }) => {
    await openPage(page, workspace("/tasks"));
    for (const [tab, empty] of [
      ["Mine", "Nothing assigned to you is open."],
      ["My team queues", "Your teams' queues are empty"],
      ["Created by me", "You haven't created any tasks yet."],
    ]) {
      await page.getByRole("tab", { name: new RegExp(`^${tab}`) }).click();
      const panel = page.getByRole("tabpanel");
      await expect(panel.getByText(empty).or(panel.getByRole("link").first())).toBeVisible();
    }
    await page.getByRole("button", { name: "New task" }).click();
    const dialog = page.getByRole("dialog", { name: "New task" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.locator(errorAlerts)).toHaveCount(0);
  });

  test("meetings: the list (or its empty state) and the way to add one", async ({ page }) => {
    await openPage(page, workspace("/meetings"));
    const list = page.getByRole("region", { name: "Recent meetings" });
    await expect(list.getByText("No meetings yet").or(list.getByRole("listitem").first())).toBeVisible();
    // Bob edits no collection, so he is told how to get to add a meeting rather than given a form.
    const add = page.getByRole("complementary", { name: "Add a meeting" });
    await expect(add).toContainText("you need edit access to one to add a meeting");
    await expect(page.locator(errorAlerts)).toHaveCount(0);
  });
});

test.describe("usage: who sees whose usage", () => {
  test("erin (admin) sees the whole organization, with the chargeback report behind the Enterprise gate", async ({ pageAs }) => {
    const { page } = await pageAs(USERS.erin);
    await openPage(page, workspace("/analytics"));
    await expect(page.getByRole("heading", { level: 2, name: "Usage across the whole organization" })).toBeVisible();
    await expect(page.getByText("Every model request made by anyone in Acme Robotics.")).toBeVisible();
    await expectGate(page, await editionOf(page), "chargeback_reports", page.getByRole("link", { name: "Download chargeback CSV" }));
  });

  test("hannah (finance manager) sees her department", async ({ pageAs }) => {
    const { page } = await pageAs(USERS.hannah);
    await openPage(page, workspace("/analytics"));
    await expect(page.getByRole("heading", { level: 2, name: "Usage in Finance" })).toBeVisible();
    await expect(page.getByText("Requests by the people in Finance, the department you manage.")).toBeVisible();
    await expect(page.getByRole("heading", { name: /^Chargeback report/ })).toHaveCount(0);
  });

  test("bob (member) sees only his own", async ({ pageAs }) => {
    const { page } = await pageAs(USERS.bob);
    await openPage(page, workspace("/analytics"));
    await expect(page.getByRole("heading", { level: 2, name: "Your own usage" })).toBeVisible();
    await expect(page.getByText(/^Only your own requests\./)).toBeVisible();
    await expect(page.getByRole("heading", { name: /^Chargeback report/ })).toHaveCount(0);
    await settled(page);
  });
});
