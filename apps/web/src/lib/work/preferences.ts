/**
 * Settings › Notifications: where a person's notifications go besides the app (email now or in a
 * daily digest, Slack direct messages), per category, and their backup approver. Shapes mirror
 * apps/api/src/workmind/api/v1/automation.py (`/me/work-preferences`). Pure helpers; the card in
 * components/settings/notifications-card.tsx renders them.
 */

import { apiJson } from "@/lib/api";

export type NotificationCategory = "approvals" | "assignments" | "escalations" | "reviews" | "sources" | "legal_holds" | "budgets" | "mentions" | "other";
export type EmailMode = "off" | "immediate" | "digest";

export interface Channels {
  email: EmailMode;
  slack: boolean;
}

export interface CategoryInfo {
  key: NotificationCategory;
  label: string;
  description: string;
}

export interface WorkPreferences {
  channels: Record<NotificationCategory, Channels>;
  /** UTC hour of the daily digest. */
  digest_hour: number;
  backup_approver_id: string | null;
  categories: CategoryInfo[];
  /** The deployment has an SMTP server: email can be chosen. */
  email_available: boolean;
  /** The organization connected a Slack bot: direct messages can be chosen. */
  slack_available: boolean;
}

export interface WorkPreferencesSave {
  channels: Partial<Record<NotificationCategory, Channels>>;
  digest_hour: number;
  backup_approver_id: string | null;
}

export const preferencesApi = {
  get: () => apiJson<WorkPreferences>("/api/v1/me/work-preferences"),
  save: (body: WorkPreferencesSave) => apiJson<WorkPreferences>("/api/v1/me/work-preferences", { method: "PUT", json: body }),
};

export const preferencesKey = "me/work-preferences";

export const EMAIL_MODE_LABELS: Record<EmailMode, string> = {
  off: "Off",
  immediate: "Right away",
  digest: "Daily digest",
};

export const EMAIL_MODES: readonly EmailMode[] = ["off", "immediate", "digest"];

/** "07:00 UTC" for the digest-hour select. */
export function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00 UTC`;
}

export const DIGEST_HOURS: readonly number[] = Array.from({ length: 24 }, (_, hour) => hour);

/** What the form edits: channels per category, the digest hour and the backup approver. */
export interface PreferencesDraft {
  channels: Record<NotificationCategory, Channels>;
  digestHour: number;
  backupApproverId: string | null;
}

export function draftFrom(preferences: WorkPreferences): PreferencesDraft {
  return {
    channels: { ...preferences.channels },
    digestHour: preferences.digest_hour,
    backupApproverId: preferences.backup_approver_id,
  };
}

export function setChannel(draft: PreferencesDraft, category: NotificationCategory, change: Partial<Channels>): PreferencesDraft {
  return { ...draft, channels: { ...draft.channels, [category]: { ...draft.channels[category], ...change } } };
}

export function toSave(draft: PreferencesDraft): WorkPreferencesSave {
  return { channels: draft.channels, digest_hour: draft.digestHour, backup_approver_id: draft.backupApproverId };
}

export function isDirty(draft: PreferencesDraft, saved: WorkPreferences): boolean {
  return JSON.stringify(toSave(draft)) !== JSON.stringify(toSave(draftFrom(saved)));
}

/** Whether any category sends a digest (then the hour matters). */
export function usesDigest(draft: PreferencesDraft): boolean {
  return Object.values(draft.channels).some((c) => c.email === "digest");
}

/**
 * A one-line summary of what leaves the app, e.g. "Email for 2 categories (1 in the digest),
 * Slack for 1". "In the app only" when nothing does.
 */
export function channelSummary(draft: PreferencesDraft): string {
  const channels = Object.values(draft.channels);
  const email = channels.filter((c) => c.email !== "off").length;
  const digest = channels.filter((c) => c.email === "digest").length;
  const slack = channels.filter((c) => c.slack).length;
  if (!email && !slack) return "In the app only.";
  const parts: string[] = [];
  if (email) parts.push(`email for ${email} ${email === 1 ? "category" : "categories"}${digest ? ` (${digest} in the digest)` : ""}`);
  if (slack) parts.push(`Slack for ${slack}`);
  const text = parts.join(", ");
  return `In the app, plus ${text}.`;
}
