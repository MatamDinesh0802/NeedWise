import { describe, expect, it } from "vitest";

import { uploadPath, type ActionItem, type Segment } from "@/lib/meetings/api";

import {
  actionEditPayload,
  actionForm,
  activeSegment,
  fileKind,
  formatDuration,
  formatTimestamp,
  jumpTarget,
  meetingLink,
  meetingPeople,
  ownerText,
  parseStartParam,
  pipelineSteps,
  speakerChanges,
  speakerName,
  titleFromFilename,
  validateUpload,
} from "./meetings";

const item = (overrides: Partial<ActionItem> = {}): ActionItem => ({
  text: "Update the AMR-500 exclusion-zone SOP",
  owner: "Wei Zhang",
  owner_user_id: "u-wei",
  due: "2026-11-05",
  task_id: null,
  segment: "T41",
  start: 271.4,
  ...overrides,
});

describe("upload", () => {
  it.each([
    ["weekly-ops-sync-2026-11-02.m4a", "audio"],
    ["Standup.MP4", "audio"],
    ["call.aiff", "audio"],
    ["notes.vtt", "transcript"],
    ["notes.srt", "transcript"],
    ["weekly-ops-sync-2026-11-02.txt", "transcript"],
    ["notes.md", "transcript"],
    ["slides.pdf", null],
    ["README", null],
  ])("fileKind(%j) → %j", (name, kind) => {
    expect(fileKind(name)).toBe(kind);
  });

  it("suggests a title from the file name", () => {
    expect(titleFromFilename("weekly-ops-sync-2026-11-02.m4a")).toBe("Weekly ops sync 2026-11-02");
    expect(titleFromFilename("board_call.vtt")).toBe("Board call");
  });

  it("needs a supported file, a collection and the consent", () => {
    expect(validateUpload({ file: null, collection: "", consent: false })).toEqual({
      file: "Choose a recording or a transcript.",
      collection: "Choose where the meeting goes.",
      consent: "Confirm that everyone in the meeting knows it is being processed.",
    });
    expect(validateUpload({ file: { name: "deck.pdf", size: 10 }, collection: "ops", consent: true }).file).toMatch(/^Use a recording/);
    expect(validateUpload({ file: { name: "a.m4a", size: 0 }, collection: "ops", consent: true })).toEqual({ file: "The file is empty." });
    expect(validateUpload({ file: { name: "a.m4a", size: 501 * 1024 * 1024 }, collection: "ops", consent: true })).toEqual({ file: "Recordings can be up to 500 MB." });
    expect(validateUpload({ file: { name: "a.m4a", size: 10 }, collection: "ops", consent: true })).toEqual({});
  });

  it("puts collection, file name and title in the query (the body is the raw file)", () => {
    expect(uploadPath({ collection: "operations-sops", filename: "weekly ops.m4a", title: " Weekly ops " })).toBe(
      "/api/v1/meetings?collection=operations-sops&filename=weekly+ops.m4a&title=Weekly+ops",
    );
    expect(uploadPath({ collection: "ops", filename: "a.vtt", title: "" })).toBe("/api/v1/meetings?collection=ops&filename=a.vtt");
  });
});

describe("status", () => {
  it("shows where processing is", () => {
    expect(pipelineSteps("transcribing").map((s) => s.state)).toEqual(["done", "current", "todo", "todo"]);
    expect(pipelineSteps("ready").map((s) => s.state)).toEqual(["done", "done", "done", "done"]);
    expect(pipelineSteps("pending").map((s) => s.state)).toEqual(["current", "todo", "todo", "todo"]);
  });
});

describe("timestamps", () => {
  it("formats times and durations", () => {
    expect(formatTimestamp(271.4)).toBe("4:31");
    expect(formatTimestamp(0)).toBe("0:00");
    expect(formatTimestamp(3723)).toBe("1:02:03");
    expect(formatDuration(1385)).toBe("23 min");
    expect(formatDuration(3900)).toBe("1 h 5 min");
    expect(formatDuration(null)).toBeNull();
  });

  it("jumps to the recording, or to the transcript when there is no recording", () => {
    expect(jumpTarget(item(), true)).toEqual({ kind: "media", seconds: 271.4, segment: "T41" });
    expect(jumpTarget(item(), false)).toEqual({ kind: "segment", segment: "T41" });
  });

  it("gives items without a valid segment no timestamp (never a made-up one)", () => {
    expect(jumpTarget(item({ segment: null, start: null }), true)).toBeNull();
    expect(jumpTarget(item({ segment: null }), false)).toBeNull();
  });

  it("finds the segment playing now", () => {
    const segments: Segment[] = [
      { id: "T1", start: 0, end: 4, speaker: "Olivia Grant", text: "Morning all." },
      { id: "T2", start: 4.2, end: 9, speaker: "Wei Zhang", text: "Morning." },
      { id: "T3", start: 12, end: 20, speaker: "Olivia Grant", text: "First, safety." },
    ];
    expect(activeSegment(segments, 0)).toBe("T1");
    expect(activeSegment(segments, 5)).toBe("T2");
    expect(activeSegment(segments, 10)).toBe("T2"); // in a gap: the one before
    expect(activeSegment(segments, 99)).toBe("T3");
    expect(activeSegment([], 3)).toBeNull();
  });

  it("links commitments to their moment, and reads it back", () => {
    expect(meetingLink("m1", 271.4)).toBe("/meetings/m1?t=271");
    expect(meetingLink("m1", null)).toBe("/meetings/m1");
    expect(parseStartParam("271")).toBe(271);
    expect(parseStartParam("abc")).toBeNull();
    expect(parseStartParam(null)).toBeNull();
  });
});

describe("speakers and owners", () => {
  const speakers = { "Olivia Grant": { name: "Olivia Grant", user_id: "u-olivia" }, "Speaker 2": { name: "Speaker 2", user_id: null } };

  it("names speakers from the mapping", () => {
    expect(speakerName("Olivia Grant", speakers)).toBe("Olivia Grant");
    expect(speakerName("Unknown", speakers)).toBe("Unknown");
    expect(speakerName(null, speakers)).toBe("Speaker");
  });

  it("offers the people this meeting already knows, plus me", () => {
    const people = meetingPeople({ speakers, action_items: [item(), item({ owner: "Bob", owner_user_id: null })] }, { id: "u-me", name: "Erin Takahashi" });
    expect(people).toEqual([
      { id: "u-me", name: "Erin Takahashi" },
      { id: "u-olivia", name: "Olivia Grant" },
      { id: "u-wei", name: "Wei Zhang" },
    ]);
  });

  it("sends only the speaker labels that changed", () => {
    expect(speakerChanges(speakers, { "Olivia Grant": "u-olivia", "Speaker 2": "u-wei" })).toEqual({ "Speaker 2": "u-wei" });
    expect(speakerChanges(speakers, { "Olivia Grant": null })).toEqual({ "Olivia Grant": null });
    expect(speakerChanges(speakers, { Nobody: "u-x" })).toEqual({});
  });

  it("says 'You' for my own items", () => {
    expect(ownerText(item(), "u-wei")).toBe("You");
    expect(ownerText(item(), "u-me")).toBe("Wei Zhang");
    expect(ownerText(item({ owner: "", owner_user_id: null }), "u-me")).toBe("Nobody yet");
  });
});

describe("correcting an action item", () => {
  it("sends only what changed", () => {
    const original = item();
    expect(actionEditPayload(original, actionForm(original))).toEqual({ ok: true, body: null });
    expect(actionEditPayload(original, { ...actionForm(original), ownerUserId: "u-olivia" })).toEqual({ ok: true, body: { owner_user_id: "u-olivia" } });
    expect(actionEditPayload(original, { text: " Update the SOP ", ownerUserId: "", due: "" })).toEqual({
      ok: true,
      body: { text: "Update the SOP", owner_user_id: null, due: null },
    });
  });

  it("checks the text and the date", () => {
    expect(actionEditPayload(item(), { text: " ", ownerUserId: "", due: "soon" })).toEqual({
      ok: false,
      errors: { text: "Describe what needs to happen.", due: "Pick a date." },
    });
  });
});
