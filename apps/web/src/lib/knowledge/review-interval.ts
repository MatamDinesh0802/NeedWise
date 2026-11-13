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

/** Escalation of overdue official reviews (docs/governance.md): days overdue before the
 * collection's owner is told (the admins as many days later), and days after the admins were told
 * before the document is demoted to reference (empty: never). */
export const ESCALATION_MIN = 1;
export const ESCALATION_MAX = 365;
export const ESCALATION_DEFAULT = 14;
export const DEMOTE_MAX = 3650;

export function escalationError(value: string): string | null {
  const text = value.trim();
  if (text === "") return "Enter a number of days: every collection escalates overdue official reviews.";
  if (!/^\d+$/.test(text)) return "Enter a whole number of days.";
  const days = Number(text);
  if (days < ESCALATION_MIN || days > ESCALATION_MAX) return `Between ${ESCALATION_MIN} and ${ESCALATION_MAX} days.`;
  return null;
}

/** Empty: never demoted automatically. */
export function demoteError(value: string): string | null {
  const text = value.trim();
  if (text === "") return null;
  if (!/^\d+$/.test(text)) return "Enter a whole number of days, or leave it empty.";
  const days = Number(text);
  if (days < 1 || days > DEMOTE_MAX) return `Between 1 and ${DEMOTE_MAX.toLocaleString("en-US")} days.`;
  return null;
}

/** "Overdue official reviews escalate after 14 days; never demoted automatically." */
export function escalationText(escalationDays: number, demoteDays: number | null | undefined): string {
  const after = `${escalationDays} ${escalationDays === 1 ? "day" : "days"}`;
  const demote = demoteDays
    ? `demoted ${demoteDays} ${demoteDays === 1 ? "day" : "days"} after the administrators are told`
    : "never demoted automatically";
  return `Overdue official reviews escalate after ${after}; ${demote}.`;
}

/** "Reviewed every 365 days", "No reviews". */
export function intervalText(days: number | null | undefined): string {
  if (!days) return "No reviews";
  return `Reviewed every ${days} ${days === 1 ? "day" : "days"}`;
}
