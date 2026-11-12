import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { ORG_ROLES } from "@/lib/session";

import {
  activeNavItem,
  findNavItem,
  isNavItemVisible,
  navItems,
  navItemsByGroup,
  siteConfig,
  visibleNavItems,
} from "./site";

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

  it("has working sections up to Phase 11 (Usage); later phases are placeholders", () => {
    const placeholder = (href: string) => readFileSync(`${appDir}${href}/page.tsx`, "utf8").includes("<PagePlaceholder");
    for (const item of navItems.filter((i) => i.href !== "/")) {
      expect(placeholder(item.href), item.href).toBe(item.phase > 11);
    }
  });

  it("no longer promises usage summaries for a later phase", () => {
    expect(findNavItem("/")?.description).not.toContain("Phase 11");
    expect(findNavItem("/analytics")?.title).toBe("Usage");
  });

  it("puts Admin under administration and Settings under account", () => {
    expect(navItemsByGroup("administration").map((i) => i.href)).toEqual(["/admin"]);
    expect(navItemsByGroup("account").map((i) => i.href)).toEqual(["/settings"]);
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

describe("role-based navigation visibility", () => {
  const admin = findNavItem("/admin")!;

  it.each([
    ["owner", true],
    ["admin", true],
    ["auditor", true],
    ["member", false],
    ["guest", false],
  ] as const)("shows Admin to %s: %s", (role, visible) => {
    expect(isNavItemVisible(admin, role)).toBe(visible);
    expect(visibleNavItems("administration", role).length > 0).toBe(visible);
  });

  it("hides Admin while loading or signed out", () => {
    expect(isNavItemVisible(admin, undefined)).toBe(false);
    expect(isNavItemVisible(admin, null)).toBe(false);
  });

  it("shows every workspace and account item to every role", () => {
    for (const role of [...ORG_ROLES, undefined]) {
      expect(visibleNavItems("workspace", role)).toEqual(navItemsByGroup("workspace"));
      expect(visibleNavItems("account", role)).toEqual(navItemsByGroup("account"));
    }
  });
});
