import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { uploadPath, type ActionItem, type Commitment, type Segment } from "@/lib/meetings/api";

import {
  actionEditPayload,
  actionForm,
  activeSegment,
  commitmentStatus,
  dueOf,
  fileKind,
  formatDay,
  formatDuration,
  formatTimestamp,
  isCommitmentClosed,
  jumpTarget,
  MEETING_DELETION_KEYS,
  meetingDeletionText,
  meetingErrorMessage,
  meetingLink,
  meetingPeople,
  mergePeople,
  ownerText,
  panelScrollTop,
  parseStartParam,
  personOptions,
  pipelineSteps,
  sortCommitments,
  speakerChanges,
  speakerName,
  stepLabel,
  titleAfterPick,
  titleFromFilename,
  untimedCount,
  uploadedText,
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

const commitment = (overrides: Partial<Commitment> = {}): Commitment => ({
  meeting_id: "m1",
  meeting_title: "Weekly ops sync",
  meeting_date: "2026-11-02",
  index: 0,
  text: "Add battery alerts",
  due: null,
  start: 6,
  task_id: null,
  task_status: null,
  ...overrides,
});

describe("upload", () => {
  it.each([
    ["weekly-ops-sync-2026-11-02.m4a", "audio"],
    ["Standup.MP4", "audio"],
    ["call.aiff", "audio"],
    ["call.aif", "audio"],
    ["board.mov", "audio"],
    ["notes.vtt", "transcript"],
    ["notes.srt", "transcript"],
    ["weekly-ops-sync-2026-11-02.txt", "transcript"],
    ["notes.md", "transcript"],
    ["slides.pdf", null],
    ["README", null],
    [".mp3", null], // like Python's PurePath(".mp3").suffix == ""
    ["notes.", null],
    ["archive.vtt.zip", null],
  ])("fileKind(%j) → %j (same lists as the API)", (name, kind) => {
    expect(fileKind(name)).toBe(kind);
  });

  it("suggests a title from the file name", () => {
    expect(titleFromFilename("weekly-ops-sync-2026-11-02.m4a")).toBe("Weekly ops sync 2026-11-02");
    expect(titleFromFilename("board_call.vtt")).toBe("Board call");
  });

  it("keeps the title in step with the file until someone types their own", () => {
    expect(titleAfterPick("", null, "board_call.vtt")).toBe("Board call");
    expect(titleAfterPick("Board call", "board_call.vtt", "ops-sync.m4a")).toBe("Ops sync"); // still the suggestion
    expect(titleAfterPick("Q4 board meeting", "board_call.vtt", "ops-sync.m4a")).toBe("Q4 board meeting"); // typed: theirs
    expect(titleAfterPick("Board call", "board_call.vtt", null)).toBe(""); // file cleared
  });

  it("needs a supported, non-empty file up to 500 MB, a collection and the consent", () => {
    expect(validateUpload({ file: null, collection: "", consent: false })).toEqual({
      file: "Choose a recording or a transcript.",
      collection: "Choose where the meeting goes.",
      consent: "Confirm that everyone in the meeting knows it is being processed.",
    });
    expect(validateUpload({ file: { name: "deck.pdf", size: 10 }, collection: "ops", consent: true }).file).toMatch(/^Use a recording/);
    expect(validateUpload({ file: { name: "a.m4a", size: 0 }, collection: "ops", consent: true })).toEqual({ file: "The file is empty." });
    expect(validateUpload({ file: { name: "a.m4a", size: 500 * 1024 * 1024 }, collection: "ops", consent: true })).toEqual({});
    expect(validateUpload({ file: { name: "a.m4a", size: 500 * 1024 * 1024 + 1 }, collection: "ops", consent: true })).toEqual({
      file: "Recordings can be up to 500 MB.",
    });
    expect(validateUpload({ file: { name: "a.m4a", size: 10 }, collection: "ops", consent: false })).toEqual({
      consent: "Confirm that everyone in the meeting knows it is being processed.",
    });
  });

  it("puts collection, file name and title in the query (the body is the raw file)", () => {
    expect(uploadPath({ collection: "operations-sops", filename: "weekly ops.m4a", title: " Weekly ops " })).toBe(
      "/api/v1/meetings?collection=operations-sops&filename=weekly+ops.m4a&title=Weekly+ops",
    );
    expect(uploadPath({ collection: "ops", filename: "a.vtt", title: "" })).toBe("/api/v1/meetings?collection=ops&filename=a.vtt");
  });

  it("says what happens next", () => {
    expect(uploadedText("audio")).toMatch(/^Transcribing the recording/);
    expect(uploadedText("transcript")).toMatch(/^Reading the transcript/);
  });

  it("words upload errors for meetings (500 MB, not the 50 MB document limit)", () => {
    const tooLarge = new ApiError({ status: 413, title: "The recording is too large", code: "too_large" });
    expect(meetingErrorMessage(tooLarge)).toMatchObject({ code: "too_large", description: expect.stringContaining("500 MB") });
    // A proxy's bare 413 has no code.
    expect(meetingErrorMessage(new ApiError({ status: 413, title: "Payload Too Large" })).code).toBe("too_large");
    expect(meetingErrorMessage(new ApiError({ status: 415, title: "x", code: "unsupported_type" })).title).toBe("File type not supported");
  });
});

describe("status", () => {
  it("shows where processing is", () => {
    expect(pipelineSteps("pending").map((s) => s.state)).toEqual(["current", "todo", "todo", "todo"]);
    expect(pipelineSteps("transcribing").map((s) => s.state)).toEqual(["done", "current", "todo", "todo"]);
    expect(pipelineSteps("extracting").map((s) => s.state)).toEqual(["done", "done", "current", "todo"]);
    expect(pipelineSteps("ready").map((s) => s.state)).toEqual(["done", "done", "done", "done"]);
  });

  it("words the transcription step for a recording or a transcript", () => {
    expect(stepLabel("transcribing", true)).toBe("Transcribing the recording");
    expect(stepLabel("transcribing", false)).toBe("Reading the transcript");
  });
});

describe("dates and timestamps", () => {
  it("formats times and durations", () => {
    expect(formatTimestamp(271.4)).toBe("4:31");
    expect(formatTimestamp(0)).toBe("0:00");
    expect(formatTimestamp(-3)).toBe("0:00");
    expect(formatTimestamp(3723)).toBe("1:02:03");
    expect(formatDuration(1385)).toBe("23 min");
    expect(formatDuration(45)).toBe("1 min");
    expect(formatDuration(3600)).toBe("1 h");
    expect(formatDuration(3900)).toBe("1 h 5 min");
    expect(formatDuration(null)).toBeNull();
  });

  it("formats a calendar day without shifting it by the viewer's time zone", () => {
    const expected = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "UTC" }).format(Date.UTC(2026, 10, 4));
    expect(formatDay("2026-11-04")).toBe(expected);
    expect(formatDay(null)).toBe("—");
    expect(formatDay("not a day")).toBe("—");
  });

  it("reads a due day like a task's (17:00 UTC)", () => {
    const now = new Date("2026-11-04T09:00:00Z");
    expect(dueOf("2026-11-05", now)).toEqual({ text: "Due tomorrow", overdue: false });
    expect(dueOf("2026-11-03", now)?.overdue).toBe(true);
    expect(dueOf(null, now)).toBeNull();
  });

  it("jumps to the recording, or to the transcript when there is no recording", () => {
    expect(jumpTarget(item(), true)).toEqual({ kind: "media", seconds: 271.4, segment: "T41" });
    expect(jumpTarget(item(), false)).toEqual({ kind: "segment", segment: "T41" });
  });

  it("gives items without a valid segment no timestamp (never a made-up one), and counts them", () => {
    expect(jumpTarget(item({ segment: null, start: null }), true)).toBeNull();
    expect(jumpTarget(item({ segment: null }), false)).toBeNull();
    expect(untimedCount({ decisions: [{ text: "d", segment: null, start: null }], action_items: [item(), item({ segment: null, start: null })] })).toBe(2);
    expect(untimedCount({ decisions: [], action_items: [item()] })).toBe(0);
  });

  it("finds the segment playing now", () => {
    const segments: Segment[] = [
      { id: "T1", start: 0, end: 4, speaker: "Olivia Grant", text: "Morning all." },
      { id: "T2", start: 4.2, end: 9, speaker: "Wei Zhang", text: "Morning." },
      { id: "T3", start: 12, end: 20, speaker: "Olivia Grant", text: "First, safety." },
    ];
    expect(activeSegment(segments, 0)).toBe("T1");
    expect(activeSegment(segments, 4.2)).toBe("T2");
    expect(activeSegment(segments, 5)).toBe("T2");
    expect(activeSegment(segments, 10)).toBe("T2"); // in a gap: the one before
    expect(activeSegment(segments, 99)).toBe("T3");
    expect(activeSegment([], 3)).toBeNull();
  });

  it("links commitments to their exact moment, and reads it back to the same segment", () => {
    const segments: Segment[] = [
      { id: "T40", start: 268, end: 271, speaker: null, text: "…" },
      { id: "T41", start: 271.4, end: 275, speaker: null, text: "I'll update the SOP." },
    ];
    expect(meetingLink("m1", 271.4)).toBe("/meetings/m1?t=271.4");
    expect(meetingLink("m1", 12.345)).toBe("/meetings/m1?t=12.35");
    expect(meetingLink("m1", 0)).toBe("/meetings/m1?t=0");
    expect(meetingLink("m1", null)).toBe("/meetings/m1");
    // Rounding down to 271 would land on T40.
    expect(activeSegment(segments, parseStartParam("271.4")!)).toBe("T41");
    expect(parseStartParam("271")).toBe(271);
    expect(parseStartParam("abc")).toBeNull();
    expect(parseStartParam("-5")).toBeNull();
    expect(parseStartParam(null)).toBeNull();
  });

  it("scrolls the transcript panel to center a segment, or just enough to follow playback", () => {
    const panel = { scrollTop: 100, clientHeight: 400 };
    // 500px below the panel's top edge, 40px tall: centered means 100 + 500 - (400 - 40) / 2.
    expect(panelScrollTop({ top: 500, height: 40 }, panel, "center")).toBe(420);
    expect(panelScrollTop({ top: 10, height: 40 }, { scrollTop: 0, clientHeight: 400 }, "center")).toBe(0); // never negative
    expect(panelScrollTop({ top: 100, height: 40 }, panel, "nearest")).toBeNull(); // already visible
    expect(panelScrollTop({ top: 380, height: 40 }, panel, "nearest")).toBe(120); // just below: bottom-align
    expect(panelScrollTop({ top: -30, height: 40 }, panel, "nearest")).toBe(70); // just above: top-align
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

  it("adds the directory, whose names win", () => {
    const merged = mergePeople(
      [
        { id: "u-wei", name: "Wei" },
        { id: "u-me", name: "Erin Takahashi" },
      ],
      [
        { id: "u-wei", display_name: "Wei Zhang" },
        { id: "u-ana", display_name: "Ana Souza" },
      ],
    );
    expect(merged).toEqual([
      { id: "u-ana", name: "Ana Souza" },
      { id: "u-me", name: "Erin Takahashi" },
      { id: "u-wei", name: "Wei Zhang" },
    ]);
  });

  it("keeps the current choice in the options even when it isn't in the list", () => {
    const people = [{ id: "u-ana", name: "Ana Souza" }];
    expect(personOptions(people, null, "Nobody")).toEqual([
      { value: "", label: "Nobody" },
      { value: "u-ana", label: "Ana Souza" },
    ]);
    expect(personOptions(people, { id: "u-gone", name: "Former colleague" }, "Nobody").at(-1)).toEqual({ value: "u-gone", label: "Former colleague" });
    expect(personOptions(people, { id: "u-ana", name: "Ana" }, "Nobody")).toHaveLength(2);
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
    expect(actionEditPayload(original, { ...actionForm(original), due: "2026-11-20" })).toEqual({ ok: true, body: { due: "2026-11-20" } });
    expect(actionEditPayload(original, { text: " Update the SOP ", ownerUserId: "", due: "" })).toEqual({
      ok: true,
      body: { text: "Update the SOP", owner_user_id: null, due: null },
    });
  });

  it("starts from the item as it is now (after a speaker mapping re-linked its owner)", () => {
    const relinked = item({ owner_user_id: "u-olivia", owner: "Olivia Grant" });
    expect(actionForm(relinked)).toEqual({ text: relinked.text, ownerUserId: "u-olivia", due: "2026-11-05" });
    expect(actionEditPayload(relinked, actionForm(relinked))).toEqual({ ok: true, body: null });
  });

  it("checks the text and the date", () => {
    expect(actionEditPayload(item(), { text: " ", ownerUserId: "", due: "soon" })).toEqual({
      ok: false,
      errors: { text: "Describe what needs to happen.", due: "Pick a date." },
    });
    expect(actionEditPayload(item(), { text: "x".repeat(501), ownerUserId: "", due: "" })).toEqual({ ok: false, errors: { text: "At most 500 characters." } });
  });

  it("explains a 409 once it is a task", () => {
    const conflict = new ApiError({ status: 409, title: "This is a task now: change the task instead", code: "already_a_task" });
    expect(meetingErrorMessage(conflict)).toMatchObject({ title: "This is a task now", code: "already_a_task" });
  });

  it("falls back to the app-wide messages", () => {
    expect(meetingErrorMessage(new ApiError({ status: 0, title: "Network error", code: "network_error" })).title).toBe("Can't reach the server");
  });
});

describe("my commitments", () => {
  it("shows the task's status, or that it isn't a task yet", () => {
    expect(commitmentStatus(commitment())).toBe("Not a task yet");
    expect(commitmentStatus(commitment({ task_id: "t1", task_status: "in_progress" }))).toBe("In progress");
    expect(commitmentStatus(commitment({ task_id: "t1", task_status: null }))).toBe("Task"); // not visible to me
    expect(isCommitmentClosed(commitment({ task_status: "cancelled" }))).toBe(true);
    expect(isCommitmentClosed(commitment({ task_status: "open" }))).toBe(false);
  });

  it("puts open ones first by due date, no deadline after, finished last", () => {
    const sorted = sortCommitments([
      commitment({ text: "done", due: "2026-11-01", task_id: "t", task_status: "done" }),
      commitment({ text: "no deadline", due: null }),
      commitment({ text: "later", due: "2026-11-20" }),
      commitment({ text: "soon", due: "2026-11-05" }),
      commitment({ text: "no deadline, newer meeting", due: null, meeting_date: "2026-11-09" }),
    ]);
    expect(sorted.map((c) => c.text)).toEqual(["soon", "later", "no deadline, newer meeting", "no deadline", "done"]);
  });
});

describe("deleting a meeting", () => {
  it("says what goes and that tasks made from it stay", () => {
    expect(meetingDeletionText(0)).toMatch(/^The meeting, its recording or transcript file, and its searchable transcript are removed/);
    expect(meetingDeletionText(0)).not.toContain("task");
    expect(meetingDeletionText(1)).toContain("The task made from one of its action items stays with its owner.");
    expect(meetingDeletionText(3)).toContain("The 3 tasks made from its action items stay with their owners.");
    expect(meetingDeletionText(3)).toMatch(/This can't be undone\.$/);
  });

  it("refreshes the lists it changes, not the deleted meeting itself", () => {
    expect(MEETING_DELETION_KEYS).toEqual(["meetings/list", "meetings/commitments", "work/tasks"]);
  });
});
