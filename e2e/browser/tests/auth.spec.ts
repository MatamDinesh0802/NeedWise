import { accountMenu, signInThroughKeycloak } from "./support/auth";
import { expect, test } from "./support/fixtures";
import { ADMIN_SECTION_LINKS } from "./support/pages";
import { ALL_USERS, seesAdministration, USERS } from "./support/users";

const WORKSPACE_LINKS = ["Overview", "Assistant", "Knowledge Hub", "AI Agents", "Approvals", "Tasks", "Meetings", "Insights", "Usage"];

// Every test here signs in from scratch, with a fresh browser context: the cached sign-ins
// the other files use are left alone, and signing out ends only this session.
for (const user of ALL_USERS) {
  test(`${user.key} (${user.roleLabel}) signs in through Keycloak, sees the right navigation and signs out`, async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("link", { name: "Sign in" }).first()).toBeVisible();

    await signInThroughKeycloak(page, user);
    await expect(page).toHaveURL(/\/$/);

    // Who the app thinks this is: name, email, organization role and departments.
    await accountMenu(page, user).click();
    const menu = page.getByRole("menu");
    await expect(menu).toContainText(user.name);
    await expect(menu).toContainText(user.email);
    await expect(menu).toContainText("Acme Robotics");
    await expect(menu).toContainText(user.roleLabel);
    for (const [department, role] of user.departments) {
      await expect(menu.getByRole("listitem").filter({ hasText: department })).toContainText(role);
    }
    if (user.departments.length === 0) await expect(menu).toContainText("None");
    await page.keyboard.press("Escape");

    // The sidebar: every workspace section for everyone, Admin only for owners, admins and auditors.
    const sidebar = page.locator('[data-sidebar="sidebar"]');
    for (const name of [...WORKSPACE_LINKS, "Settings"]) {
      await expect(sidebar.getByRole("link", { name, exact: true })).toBeVisible();
    }
    const adminLink = sidebar.getByRole("link", { name: "Admin", exact: true });
    if (seesAdministration(user)) {
      await expect(adminLink).toBeVisible();
      await adminLink.click();
      await expect(page).toHaveURL(/\/admin\/people$/);
      const sections = page.getByRole("navigation", { name: "Administration sections" }).getByRole("link");
      await expect(sections).toHaveText(ADMIN_SECTION_LINKS);
    } else {
      await expect(adminLink).toHaveCount(0);
      // Typing the URL shows a refusal, not the pages (the API refuses the data with 403).
      await page.goto("/admin");
      await expect(page.getByText("You don't have access to administration")).toBeVisible();
      await expect(page.getByRole("navigation", { name: "Administration sections" })).toHaveCount(0);
    }

    // Sign out: the WorkMind session and the Keycloak session both end.
    await accountMenu(page, user).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await expect(page.getByRole("link", { name: "Sign in" }).first()).toBeVisible();
    await expect(accountMenu(page, user)).toHaveCount(0);
    expect((await page.request.get("/api/v1/me")).status()).toBe(401);
    // Keycloak asks for the email again instead of signing straight back in.
    await page.getByRole("link", { name: "Sign in" }).first().click();
    await expect(page.getByLabel("Email")).toBeVisible();
  });
}

test("a wrong password keeps you on Keycloak's form and creates no session", async ({ page }) => {
  // Keycloak locks an account for a while after 10 failures in a row; a success resets the count.
  const user = USERS.mallory;
  await page.goto("/");
  await page.getByRole("link", { name: "Sign in" }).first().click();
  await page.getByLabel("Email").fill(user.email);
  await page.getByRole("button", { name: "Sign In" }).click();
  await page.getByLabel("Password", { exact: true }).fill("not-the-demo-password");
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByText(/invalid/i).first()).toBeVisible();
  await expect(page).toHaveURL(/\/auth\/realms\//);
  expect((await page.request.get("/api/v1/me")).status()).toBe(401);
});
