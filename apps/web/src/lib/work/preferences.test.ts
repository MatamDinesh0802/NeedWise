import { describe, expect, it } from "vitest";

import {
  channelSummary,
  draftFrom,
  hourLabel,
  isDirty,
  setChannel,
  toSave,
  usesDigest,
  type NotificationCategory,
  type WorkPreferences,
} from "./preferences";

const OFF = { email: "off", slack: false } as const;
const CATEGORIES: NotificationCategory[] = ["approvals", "assignments", "escalations", "reviews", "sources", "legal_holds", "budgets", "mentions", "other"];

const saved = (): WorkPreferences => ({
  channels: Object.fromEntries(CATEGORIES.map((c) => [c, { ...OFF }])) as WorkPreferences["channels"],
  digest_hour: 8,
  backup_approver_id: null,
  categories: CATEGORIES.map((key) => ({ key, label: key, description: "" })),
  email_available: true,
  slack_available: false,
});

describe("notification preferences", () => {
  it("starts from what is saved and knows when it changed", () => {
    const draft = draftFrom(saved());
    expect(isDirty(draft, saved())).toBe(false);
    const changed = setChannel(draft, "approvals", { email: "immediate" });
    expect(isDirty(changed, saved())).toBe(true);
    expect(changed.channels.assignments).toEqual(OFF);
    expect(draft.channels.approvals.email).toBe("off"); // not mutated
  });

  it("sends every category, the hour and the backup approver", () => {
    const draft = { ...setChannel(draftFrom(saved()), "mentions", { slack: true }), backupApproverId: "u-ivan", digestHour: 6 };
    const body = toSave(draft);
    expect(body.channels.mentions).toEqual({ email: "off", slack: true });
    expect(Object.keys(body.channels)).toHaveLength(9);
    expect(body).toMatchObject({ digest_hour: 6, backup_approver_id: "u-ivan" });
  });

  it("says in one line what leaves the app", () => {
    let draft = draftFrom(saved());
    expect(channelSummary(draft)).toBe("In the app only.");
    expect(usesDigest(draft)).toBe(false);
    draft = setChannel(draft, "approvals", { email: "digest" });
    draft = setChannel(draft, "escalations", { email: "immediate", slack: true });
    expect(usesDigest(draft)).toBe(true);
    expect(channelSummary(draft)).toBe("In the app, plus email for 2 categories (1 in the digest), Slack for 1.");
  });

  it("labels digest hours in UTC", () => {
    expect(hourLabel(7)).toBe("07:00 UTC");
    expect(hourLabel(18)).toBe("18:00 UTC");
  });
});
