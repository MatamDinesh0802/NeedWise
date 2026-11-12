import { describe, expect, it } from "vitest";

import {
  confirmMatches,
  countsSummary,
  draftChanged,
  draftErrors,
  draftFromPolicy,
  draftToBody,
  keepsEverything,
  lastRunSummary,
  type RetentionPolicyBody,
} from "./retention";

const saved: RetentionPolicyBody = {
  conversations_days: 365,
  analytics_runs_days: null,
  meeting_recordings_days: 90,
  read_notifications_days: null,
};

describe("the retention form", () => {
  it("starts from the saved policy, empty meaning keep forever", () => {
    expect(draftFromPolicy(saved)).toEqual({ conversations: "365", analytics_runs: "", meeting_recordings: "90", read_notifications: "" });
    expect(draftFromPolicy(null)).toEqual({ conversations: "", analytics_runs: "", meeting_recordings: "", read_notifications: "" });
  });

  it("refuses anything under 30 days, fractions and words, like the API", () => {
    const errors = draftErrors({ conversations: "29", analytics_runs: "45.5", meeting_recordings: "a year", read_notifications: "36501" });
    expect(errors.conversations).toBe("At least 30 days.");
    expect(errors.analytics_runs).toContain("whole number");
    expect(errors.meeting_recordings).toContain("whole number");
    expect(errors.read_notifications).toBe("At most 36,500 days.");
    expect(draftErrors({ conversations: "30", analytics_runs: "", meeting_recordings: " 90 ", read_notifications: "" })).toEqual({});
  });

  it("sends numbers and nulls", () => {
    expect(draftToBody({ conversations: "30", analytics_runs: "", meeting_recordings: " 90 ", read_notifications: "" })).toEqual({
      conversations_days: 30,
      analytics_runs_days: null,
      meeting_recordings_days: 90,
      read_notifications_days: null,
    });
  });

  it("knows when there are unsaved changes", () => {
    expect(draftChanged(draftFromPolicy(saved), saved)).toBe(false);
    expect(draftChanged({ ...draftFromPolicy(saved), conversations: " 365 " }, saved)).toBe(false);
    expect(draftChanged({ ...draftFromPolicy(saved), conversations: "400" }, saved)).toBe(true);
    expect(draftChanged(draftFromPolicy(null), null)).toBe(false);
  });

  it("knows a policy that keeps everything deletes nothing", () => {
    expect(keepsEverything(null)).toBe(true);
    expect(keepsEverything({ ...saved, conversations_days: null, meeting_recordings_days: null })).toBe(true);
    expect(keepsEverything(saved)).toBe(false);
  });
});

describe("what a run deletes, in words", () => {
  it("lists the kinds that have something, with singular and plural", () => {
    expect(countsSummary({ conversations: 2, analytics_runs: 0, meeting_recordings: 1, read_notifications: 0 })).toBe(
      "2 conversations and 1 meeting recording",
    );
    expect(countsSummary({ conversations: 1, analytics_runs: 3, meeting_recordings: 0, read_notifications: 1200 })).toBe(
      "1 conversation, 3 Insights questions and 1,200 read notifications",
    );
    expect(countsSummary({ conversations: 0, analytics_runs: 0, meeting_recordings: 0, read_notifications: 0 })).toBe("nothing");
    expect(countsSummary(undefined)).toBe("nothing");
  });

  it("describes the last run, or that there wasn't one", () => {
    expect(lastRunSummary({ last_run_at: null, last_run: {} })).toContain("has not run yet");
    const text = lastRunSummary({ last_run_at: "2026-11-11T02:00:00Z", last_run: { trigger: "schedule", deleted: { conversations: 4 } } });
    expect(text).toContain("(the daily run): deleted 4 conversations.");
    expect(lastRunSummary({ last_run_at: "2026-11-11T02:00:00Z", last_run: { trigger: "manual", deleted: {} } })).toContain(
      "(run by hand): deleted nothing.",
    );
  });

  it("asks for the exact word before deleting", () => {
    expect(confirmMatches("delete")).toBe(true);
    expect(confirmMatches("  delete ")).toBe(true);
    expect(confirmMatches("Delete")).toBe(false);
    expect(confirmMatches("del")).toBe(false);
  });
});
