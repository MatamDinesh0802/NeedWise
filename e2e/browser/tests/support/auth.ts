import { promises as fs } from "node:fs";
import path from "node:path";

import { expect, request, type Browser, type Page } from "@playwright/test";

import { DEMO_PASSWORD, type DemoUser } from "./users";

/** Cached sign-ins, one storage state per user (git-ignored: it holds session cookies). */
export const AUTH_DIR = path.resolve(__dirname, "..", "..", ".auth");

/** The account menu's button: it is there once the app knows who is signed in. */
export function accountMenu(page: Page, user: DemoUser) {
  return page.getByRole("button", { name: `Account menu for ${user.name}` });
}

/**
 * Signs in the way a person does: the header's "Sign in" link, Keycloak's email page, then
 * its password page (Keycloak Organizations asks for the email first), back to the app.
 */
export async function signInThroughKeycloak(page: Page, user: DemoUser, startAt = "/"): Promise<void> {
  await page.goto(startAt);
  // The header's link comes first; a signed-out page may offer a second one in its content.
  await page.getByRole("link", { name: "Sign in" }).first().click();
  await expect(page).toHaveURL(/\/auth\/realms\/[^/]+\/protocol\/openid-connect\/auth/);
  await page.getByLabel("Email").fill(user.email);
  await page.getByRole("button", { name: "Sign In" }).click();
  await page.getByLabel("Password", { exact: true }).fill(DEMO_PASSWORD);
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page).not.toHaveURL(/\/auth\/realms\//);
  await expect(accountMenu(page, user)).toBeVisible();
}

const validated = new Map<string, number>();
const REVALIDATE_MS = 60_000;

async function stillSignedIn(file: string, user: DemoUser, baseURL: string): Promise<boolean> {
  try {
    await fs.access(file);
  } catch {
    return false;
  }
  const api = await request.newContext({ baseURL, storageState: file });
  try {
    const me = await api.get("/api/v1/me");
    return me.ok() && ((await me.json()) as { email?: string }).email === user.email;
  } catch {
    return false;
  } finally {
    await api.dispose();
  }
}

/**
 * A storage state file for `user`, signing in through Keycloak only when there is no cached
 * one or its session has ended (30 minutes idle, or a logout). Workers may sign the same user
 * in at the same time: each gets its own valid session, and the file is replaced atomically.
 */
export async function storageStateFor(browser: Browser, user: DemoUser, baseURL: string): Promise<string> {
  // One folder per stack: a session from one is unknown to another.
  const file = path.join(AUTH_DIR, new URL(baseURL).host.replace(/[^\w.-]/g, "_"), `${user.key}.json`);
  const checked = validated.get(file);
  if (checked !== undefined && Date.now() - checked < REVALIDATE_MS) return file;
  if (!(await stillSignedIn(file, user, baseURL))) {
    await fs.mkdir(path.dirname(file), { recursive: true });
    // Explicitly signed out: inside a test, browser.newContext() inherits the test's options,
    // including the storage state of the user the test runs as.
    const context = await browser.newContext({ baseURL, storageState: { cookies: [], origins: [] } });
    try {
      await signInThroughKeycloak(await context.newPage(), user);
      const temporary = `${file}.${process.pid}.${Date.now()}.tmp`;
      await context.storageState({ path: temporary });
      await fs.rename(temporary, file);
    } finally {
      await context.close();
    }
  }
  validated.set(file, Date.now());
  return file;
}
