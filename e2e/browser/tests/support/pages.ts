import { expect, type Page } from "@playwright/test";

/**
 * The app's pages and what each must show once it has loaded. The header's <h1> names the
 * section (apps/web/src/config/site.ts); the page's own heading is an <h2>, or an <h3> inside
 * the Administration frame.
 */
export interface AppPage {
  path: string;
  /** The section title in the header (the sidebar entry). */
  section: string;
  heading: { name: string | RegExp; level: number };
}

export const WORKSPACE_PAGES: readonly AppPage[] = [
  { path: "/", section: "Overview", heading: { name: "Overview", level: 2 } },
  { path: "/chat", section: "Assistant", heading: { name: "What do you want to know?", level: 2 } },
  { path: "/knowledge", section: "Knowledge Hub", heading: { name: "Knowledge Hub", level: 2 } },
  { path: "/knowledge/governance", section: "Knowledge Hub", heading: { name: "Governance", level: 2 } },
  { path: "/agents", section: "AI Agents", heading: { name: "AI Agents", level: 2 } },
  { path: "/approvals", section: "Approvals", heading: { name: "Approvals", level: 2 } },
  { path: "/tasks", section: "Tasks", heading: { name: "Tasks", level: 2 } },
  { path: "/meetings", section: "Meetings", heading: { name: "Meetings", level: 2 } },
  { path: "/insights", section: "Insights", heading: { name: "Insights", level: 2 } },
  { path: "/analytics", section: "Usage", heading: { name: /^(Usage across the whole organization|Usage in .+|Your own usage)$/, level: 2 } },
  { path: "/settings", section: "Settings", heading: { name: "Settings", level: 2 } },
];

/** Admin sections, in the order of the Administration navigation (lib/admin/sections.ts). */
export const ADMIN_PAGES: readonly AppPage[] = [
  ["/admin/people", "People"],
  ["/admin/departments", "Departments"],
  ["/admin/groups", "Groups"],
  ["/admin/collections", "Collections"],
  ["/admin/governance", "Knowledge governance"],
  ["/admin/sources", "Sources"],
  ["/admin/agents", "Agents"],
  ["/admin/workflows", "Workflows"],
  ["/admin/analytics", "Analytics"],
  ["/admin/models", "Models"],
  ["/admin/budgets", "Budgets"],
  ["/admin/audit", "Audit log"],
  ["/admin/retention", /^Retention/],
].map(([path, heading]) => ({ path: path as string, section: "Admin", heading: { name: heading, level: 3 } }));

/** The administration navigation's link names, in order. */
export const ADMIN_SECTION_LINKS = [
  "People",
  "Departments",
  "Groups",
  "Collections",
  "Governance",
  "Sources",
  "Agents",
  "Workflows",
  "Analytics",
  "Models",
  "Budgets",
  "Audit log",
  "Retention",
];

/** The pages the accessibility and layout checks cover. */
export const MAIN_PATHS = ["/", "/chat", "/knowledge", "/agents", "/approvals", "/tasks", "/meetings", "/insights", "/analytics", "/settings"];
export const MAIN_ADMIN_PATHS = ["/admin/people", "/admin/agents", "/admin/models", "/admin/budgets", "/admin/governance", "/admin/audit", "/admin/retention"];

export function sectionTitle(page: Page) {
  return page.getByRole("heading", { level: 1 });
}

/** Opens `target` and waits until its own heading shows and nothing is loading any more. */
export async function openPage(page: Page, target: AppPage): Promise<void> {
  await page.goto(target.path);
  await expectRendered(page, target);
}

export async function expectRendered(page: Page, target: AppPage): Promise<void> {
  await expect(sectionTitle(page)).toHaveText(target.section);
  await expect(page.getByRole("heading", { level: target.heading.level, name: target.heading.name }).first()).toBeVisible();
  await settled(page);
}

/** No skeletons or busy regions left, and the network quiet. */
export async function settled(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  await expect(page.locator('[aria-busy="true"]:visible')).toHaveCount(0);
}
