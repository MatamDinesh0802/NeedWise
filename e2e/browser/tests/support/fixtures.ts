import { test as base, expect, type APIResponse, type BrowserContext, type Page } from "@playwright/test";

import { storageStateFor } from "./auth";
import type { DemoUser } from "./users";

/**
 * Statuses the app expects and handles: not signed in (401), not licensed (402, Enterprise
 * features in Community) and not allowed (403, e.g. admin data for a member).
 */
export const EXPECTED_STATUSES: readonly number[] = [401, 402, 403];

interface Allowance {
  status: number;
  url: RegExp;
}

/**
 * Watches a page for what a person would never notice but should never happen: console
 * errors, uncaught exceptions, failed requests (4xx other than 401/402/403, any 5xx) and
 * network failures on our own origin. Every test runs with it; a test that provokes another
 * status on purpose (a 404 for a collection someone may not see) allows it explicitly.
 */
export class PageGuard {
  private readonly problems: string[] = [];
  private readonly allowances: Allowance[] = [];

  constructor(
    page: Page,
    private readonly origin: string,
  ) {
    page.on("pageerror", (error) => this.problems.push(`uncaught exception: ${error.message}`));
    page.on("console", (message) => {
      if (message.type() !== "error") return;
      const text = message.text();
      // The browser logs every 4xx/5xx as "Failed to load resource"; the response rule decides.
      if (text.startsWith("Failed to load resource:")) return;
      this.problems.push(`console error on ${message.location().url || page.url()}: ${text}`);
    });
    page.on("response", (response) => {
      const status = response.status();
      const url = response.url();
      if (status < 400 || !url.startsWith(this.origin) || this.allowed(status, url)) return;
      this.problems.push(`HTTP ${status} ${response.request().method()} ${url}`);
    });
    page.on("requestfailed", (request) => {
      const error = request.failure()?.errorText ?? "";
      // Navigations cancel in-flight requests (polls, event streams): that is not a failure.
      if (!request.url().startsWith(this.origin) || /ERR_ABORTED|NS_BINDING_ABORTED|cancelled/i.test(error)) return;
      this.problems.push(`request failed: ${request.method()} ${request.url()} (${error})`);
    });
  }

  /** Accept `status` for URLs matching `url` in this test (e.g. a 404 the test provokes). */
  allow(status: number, url: RegExp): void {
    this.allowances.push({ status, url });
  }

  private allowed(status: number, url: string): boolean {
    if (EXPECTED_STATUSES.includes(status)) return true;
    return this.allowances.some((a) => a.status === status && a.url.test(url));
  }

  /** Everything recorded so far; reading it clears it. */
  take(): string[] {
    return this.problems.splice(0);
  }
}

interface Fixtures {
  /** Who is signed in for the test; null runs it signed out. Set it with test.use(). */
  user: DemoUser | null;
  /** The guard on `page`. */
  guard: PageGuard;
  /** A page in its own browser context, signed in as someone else, with its own guard. */
  pageAs: (user: DemoUser) => Promise<{ page: Page; guard: PageGuard }>;
  guards: PageGuard[];
}

export const test = base.extend<Fixtures>({
  user: [null, { option: true }],

  storageState: async ({ user, browser, baseURL }, use) => {
    await use(user ? await storageStateFor(browser, user, baseURL!) : { cookies: [], origins: [] });
  },

  guards: [
    async ({}, use, testInfo) => {
      const guards: PageGuard[] = [];
      await use(guards);
      const problems = guards.flatMap((g) => g.take());
      if (problems.length > 0) {
        await testInfo.attach("page problems", { body: problems.join("\n"), contentType: "text/plain" });
      }
      expect(problems, "console errors, uncaught exceptions or failed requests").toEqual([]);
    },
    { auto: true },
  ],

  guard: [
    async ({ page, baseURL, guards }, use) => {
      const guard = new PageGuard(page, new URL(baseURL!).origin);
      guards.push(guard);
      await use(guard);
    },
    { auto: true },
  ],

  pageAs: async ({ browser, baseURL, guards }, use) => {
    const contexts: BrowserContext[] = [];
    await use(async (user) => {
      const context = await browser.newContext({ baseURL, storageState: await storageStateFor(browser, user, baseURL!) });
      contexts.push(context);
      const page = await context.newPage();
      const guard = new PageGuard(page, new URL(baseURL!).origin);
      guards.push(guard);
      return { page, guard };
    });
    for (const context of contexts) await context.close();
  },
});

export { expect };

/** Headers the API wants on a cookie-authenticated change (apps/api/src/needwise/api/deps.py). */
export function sameOrigin(baseURL: string): Record<string, string> {
  return { Origin: new URL(baseURL).origin, "X-CSRF-Protection": "1" };
}

export async function json<T>(response: APIResponse): Promise<T> {
  expect(response.ok(), `${response.url()} answered ${response.status()}: ${await response.text()}`).toBe(true);
  return (await response.json()) as T;
}
