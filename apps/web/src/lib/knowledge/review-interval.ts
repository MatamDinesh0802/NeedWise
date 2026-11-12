/** A collection's review interval in days (1 to 3650, or none), as typed in a form. */

export const INTERVAL_MIN = 1;
export const INTERVAL_MAX = 3650;

/** Empty: no reviews (null). Call only when intervalError() is null. */
export function parseInterval(value: string): number | null {
  const text = value.trim();
  return text === "" ? null : Number(text);
}

export function intervalError(value: string): string | null {
  const text = value.trim();
  if (text === "") return null;
  if (!/^\d+$/.test(text)) return "Enter a whole number of days, or leave it empty.";
  const days = Number(text);
  if (days < INTERVAL_MIN || days > INTERVAL_MAX) return `Between ${INTERVAL_MIN} and ${INTERVAL_MAX.toLocaleString("en-US")} days.`;
  return null;
}

/** "Reviewed every 365 days", "No reviews". */
export function intervalText(days: number | null | undefined): string {
  if (!days) return "No reviews";
  return `Reviewed every ${days} ${days === 1 ? "day" : "days"}`;
}
