import { describe, expect, it } from "vitest";

import { isValidSlug, SLUG_MAX_LENGTH, slugError, suggestSlug } from "./slug";

describe("isValidSlug", () => {
  it.each(["hr", "people-hr", "team-2026", "a", "a1-b2-c3"])("accepts %s", (slug) => {
    expect(isValidSlug(slug)).toBe(true);
    expect(slugError(slug)).toBeNull();
  });

  it.each([
    ["", "Enter a slug."],
    ["People", "Use lowercase letters only."],
    ["-hr", "Don't start or end with a hyphen."],
    ["hr-", "Don't start or end with a hyphen."],
    ["people--hr", "Don't use two hyphens in a row."],
    ["people_hr", "Use only lowercase letters, numbers and single hyphens."],
    ["people hr", "Use only lowercase letters, numbers and single hyphens."],
    ["ñandu", "Use only lowercase letters, numbers and single hyphens."],
  ])("rejects %j with %j", (slug, message) => {
    expect(isValidSlug(slug)).toBe(false);
    expect(slugError(slug)).toBe(message);
  });

  it("enforces the API's 64-character limit", () => {
    expect(isValidSlug("a".repeat(SLUG_MAX_LENGTH))).toBe(true);
    expect(isValidSlug("a".repeat(SLUG_MAX_LENGTH + 1))).toBe(false);
    expect(slugError("a".repeat(SLUG_MAX_LENGTH + 1))).toMatch(/64/);
  });

  it("matches the API pattern exactly", () => {
    // ^[a-z0-9]+(-[a-z0-9]+)*$ from apps/api identity models
    expect(isValidSlug("0")).toBe(true);
    expect(isValidSlug("a-b-")).toBe(false);
  });
});

describe("suggestSlug", () => {
  it.each([
    ["People & HR", "people-hr"],
    ["Engineering", "engineering"],
    ["  Customer   Support  ", "customer-support"],
    ["Ingeniería Ñandú", "ingenieria-nandu"],
    ["R&D / Labs (2026)", "r-d-labs-2026"],
    ["Café—Bar", "cafe-bar"],
    ["!!!", ""],
    ["", ""],
  ])("%j → %j", (name, slug) => {
    expect(suggestSlug(name)).toBe(slug);
  });

  it("always produces a valid slug (or nothing)", () => {
    for (const name of ["Ops", "A -- B", "--x--", "Über Team", "日本 Team", "x".repeat(100)]) {
      const slug = suggestSlug(name);
      expect(slug === "" || isValidSlug(slug), `${name} → ${slug}`).toBe(true);
    }
  });

  it("truncates to 64 characters without a trailing hyphen", () => {
    const slug = suggestSlug(`${"a".repeat(63)} bcd`);
    expect(slug.length).toBeLessThanOrEqual(SLUG_MAX_LENGTH);
    expect(slug.endsWith("-")).toBe(false);
    expect(isValidSlug(slug)).toBe(true);
  });
});
