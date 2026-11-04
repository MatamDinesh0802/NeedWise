/** Slugs for departments and groups. Must match the API's SLUG_PATTERN. */

export const SLUG_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/;
export const SLUG_MAX_LENGTH = 64;

export function isValidSlug(slug: string): boolean {
  return slug.length <= SLUG_MAX_LENGTH && SLUG_PATTERN.test(slug);
}

/** A human-readable reason the slug is invalid, or null if it is valid. */
export function slugError(slug: string): string | null {
  if (slug === "") return "Enter a slug.";
  if (slug.length > SLUG_MAX_LENGTH) return `Use at most ${SLUG_MAX_LENGTH} characters.`;
  if (/[A-Z]/.test(slug)) return "Use lowercase letters only.";
  if (slug.startsWith("-") || slug.endsWith("-")) return "Don't start or end with a hyphen.";
  if (slug.includes("--")) return "Don't use two hyphens in a row.";
  if (!SLUG_PATTERN.test(slug)) return "Use only lowercase letters, numbers and single hyphens.";
  return null;
}

/**
 * Suggests a slug from a display name: "People & HR" → "people-hr",
 * "Ingeniería Ñandú" → "ingenieria-nandu". Returns "" if nothing usable remains.
 */
export function suggestSlug(name: string): string {
  const slug = name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "") // strip combining accents
    .toLowerCase()
    .replace(/&/g, " ")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug.slice(0, SLUG_MAX_LENGTH).replace(/-+$/, "");
}
