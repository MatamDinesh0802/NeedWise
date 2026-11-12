import type { Page } from "@playwright/test";

import { expect, test } from "./support/fixtures";
import { MAIN_ADMIN_PATHS, MAIN_PATHS, settled } from "./support/pages";
import { USERS } from "./support/users";

// No horizontal page scroll on a phone (375 px) or a laptop (1280 px). Wide tables may
// scroll inside their own container; the page itself must not.
test.describe.configure({ mode: "parallel" });

async function horizontalOverflow(page: Page): Promise<{ overflow: number; culprits: string[] }> {
  return page.evaluate(() => {
    const viewport = document.documentElement.clientWidth;
    const culprits: string[] = [];
    for (const element of Array.from(document.body.querySelectorAll<HTMLElement>("*"))) {
      const box = element.getBoundingClientRect();
      if (box.width > 0 && box.right > viewport + 1 && getComputedStyle(element).position !== "fixed") {
        culprits.push(`${element.tagName.toLowerCase()}[data-slot=${element.dataset.slot ?? ""}] "${(element.textContent ?? "").trim().slice(0, 40)}"`);
      }
    }
    return { overflow: document.documentElement.scrollWidth - viewport, culprits: culprits.slice(0, 5) };
  });
}

for (const width of [375, 1280]) {
  test.describe(`${width} px wide`, () => {
    for (const [user, paths] of [
      [USERS.bob, MAIN_PATHS],
      [USERS.erin, MAIN_ADMIN_PATHS],
    ] as const) {
      test.describe(`as ${user.key}`, () => {
        test.use({ user, viewport: { width, height: 800 } });
        for (const path of paths) {
          test(`${path} does not scroll sideways`, async ({ page }) => {
            await page.goto(path);
            await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
            await settled(page);
            const { overflow, culprits } = await horizontalOverflow(page);
            expect(overflow, `the page is ${overflow}px wider than the window; widest: ${culprits.join(", ")}`).toBeLessThanOrEqual(0);
          });
        }
      });
    }
  });
}

test.describe("375 px wide", () => {
  test.use({ user: USERS.bob, viewport: { width: 375, height: 800 } });

  test("the sidebar becomes a menu that opens over the page", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Overview");
    await page.getByRole("button", { name: "Toggle Sidebar" }).click();
    const sidebar = page.getByRole("dialog", { name: "Sidebar" });
    await expect(sidebar).toBeVisible();
    await sidebar.getByRole("link", { name: "Knowledge Hub" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Knowledge Hub");
  });
});
