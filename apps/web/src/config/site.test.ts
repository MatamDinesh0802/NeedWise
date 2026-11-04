import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { activeNavItem, findNavItem, navItems, navItemsByGroup, siteConfig } from "./site";

const appDir = fileURLToPath(new URL("../app", import.meta.url));

describe("navigation config", () => {
  it("has unique hrefs and titles", () => {
    expect(new Set(navItems.map((i) => i.href)).size).toBe(navItems.length);
    expect(new Set(navItems.map((i) => i.title)).size).toBe(navItems.length);
  });

  it("gives every item a description and a valid roadmap phase (0–12)", () => {
    for (const item of navItems) {
      expect(item.description.length, item.href).toBeGreaterThan(10);
      expect(Number.isInteger(item.phase), item.href).toBe(true);
      expect(item.phase, item.href).toBeGreaterThanOrEqual(0);
      expect(item.phase, item.href).toBeLessThanOrEqual(12);
    }
  });

  it("has a page file for every nav item", () => {
    for (const item of navItems) {
      const page = item.href === "/" ? `${appDir}/page.tsx` : `${appDir}${item.href}/page.tsx`;
      expect(existsSync(page), `missing ${page}`).toBe(true);
    }
  });

  it("puts Admin and Settings in the administration group", () => {
    expect(navItemsByGroup("administration").map((i) => i.href)).toEqual(["/admin", "/settings"]);
    expect(navItemsByGroup("workspace")).toHaveLength(navItems.length - 2);
  });

  it("keeps the product name in one place", () => {
    expect(siteConfig.name).toContain(siteConfig.shortName);
  });
});

describe("activeNavItem", () => {
  it("matches the overview only on the root path", () => {
    expect(activeNavItem("/")?.href).toBe("/");
    expect(activeNavItem("/unknown")).toBeUndefined();
  });

  it("matches nested routes to their section", () => {
    expect(activeNavItem("/knowledge")?.href).toBe("/knowledge");
    expect(activeNavItem("/knowledge/documents/42")?.href).toBe("/knowledge");
  });

  it("does not match on a shared prefix that is not a path segment", () => {
    expect(activeNavItem("/administrators")).toBeUndefined();
  });

  it("finds items by exact href", () => {
    expect(findNavItem("/agents")?.title).toBe("AI Agents");
    expect(findNavItem("/agents/hr")).toBeUndefined();
  });
});
