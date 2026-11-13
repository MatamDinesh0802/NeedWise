import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Meeting } from "@/lib/meetings/api";
import type { Me } from "@/lib/session";
import { alice } from "@/test/fixtures";

import { ReadyMeeting } from "./meeting-detail";

const me: Me = { ...alice, departments: [...alice.departments], groups: [...alice.groups] };

const meeting = (overrides: Partial<Meeting> = {}): Meeting => ({
  id: "m1",
  title: "Weekly ops sync",
  collection_id: "c1",
  owner_id: alice.id,
  status: "ready",
  error: null,
  filename: "weekly-ops.vtt",
  has_media: false,
  duration_s: 334,
  language: "en",
  segments: [
    { id: "T1", start: 1, end: 5, speaker: "Olivia Grant", text: "The drill moves to Tuesday mornings." },
    { id: "T2", start: 6, end: 10, speaker: "Speaker 2", text: "I'll add battery alerts by Friday next week." },
  ],
  speakers: {
    "Olivia Grant": { name: "Olivia Grant", user_id: "u-olivia" },
    "Speaker 2": { name: "Wei Zhang", user_id: "u-wei" },
  },
  summary: "The drill moves to Tuesday; Wei adds battery alerts.",
  decisions: [
    { text: "Move the Rotterdam safety drill to Tuesday mornings", segment: "T1", start: 1 },
    { text: "An idea nobody pinned to a moment", segment: null, start: null },
  ],
  action_items: [
    { text: "Add battery-temperature alerts", owner: "Wei Zhang", owner_user_id: "u-wei", due: "2026-11-13", task_id: null, segment: "T2", start: 6 },
    { text: "Update the operations SOP", owner: "Olivia", owner_user_id: null, due: null, task_id: "task-9", segment: null, start: null },
  ],
  topics: [],
  document_id: null,
  timings: {},
  can_edit: true,
  can_map_speakers: true,
  held_on: "2026-11-02",
  created_at: "2026-11-04T09:00:00Z",
  ...overrides,
});

const render = (m: Meeting) => renderToStaticMarkup(<ReadyMeeting meeting={m} me={me} startAt={null} />);

describe("ReadyMeeting", () => {
  it("shows the summary, decisions and action items, with names from the speaker mapping", () => {
    const html = render(meeting());
    expect(html).toContain("The drill moves to Tuesday; Wei adds battery alerts.");
    expect(html).toContain("Move the Rotterdam safety drill to Tuesday mornings");
    expect(html).toContain("Add battery-temperature alerts");
    expect(html).toContain("Wei Zhang: </span>I&#x27;ll add battery alerts"); // "Speaker 2" mapped to Wei
  });

  it("gives a timestamp only to items with a segment, and says why the others have none", () => {
    const html = render(meeting());
    // Transcript upload: a timestamp shows the segment in the transcript.
    expect(html.match(/aria-label="Show 0:0\d in the transcript"/g)).toHaveLength(2); // T1 decision, T2 action
    expect(html).toContain("2 items have no timestamp");
    expect(html).not.toContain("<audio");
  });

  it("plays the recording from a timestamp when there is one", () => {
    const html = render(meeting({ has_media: true, filename: "weekly-ops.m4a" }));
    expect(html).toContain('src="/api/v1/meetings/m1/media"');
    expect(html).toContain('aria-label="Play the recording from 0:06"');
    expect(html).not.toMatch(/aria-label="Show [\d:]+ in the transcript"/);
  });

  it("links an item that is already a task, and offers editors corrections and tasks for the rest", () => {
    const html = render(meeting());
    expect(html).toContain('href="/tasks/task-9"');
    expect(html).toContain("Make task");
    expect(html).toContain("Correct");
    expect(html).toContain("Who is who");
    expect(html).toContain("(not linked to a colleague)");
  });

  it("shows viewers the notes without the editing controls", () => {
    const html = render(meeting({ can_edit: false, can_map_speakers: false }));
    expect(html).not.toContain("Make task");
    expect(html).not.toContain("Who is who");
    expect(html).toContain('href="/tasks/task-9"');
  });

  it("leaves naming speakers to those who may: an editor who wasn't there corrects items only", () => {
    const html = render(meeting({ can_map_speakers: false }));
    expect(html).toContain("Make task");
    expect(html).not.toContain("Who is who");
  });

  it("outlines the topics, each a button to its moment, and marks where each starts in the transcript", () => {
    const html = render(
      meeting({
        topics: [
          { title: "Safety drill", segment: "T1", start: 1, end: 5, source: "model" },
          { title: "Battery alerts for SPEAKER_2", segment: "T2", start: 6, end: 10, source: "model" },
        ],
      }),
    );
    expect(html).toContain('id="topics-heading"');
    expect(html).toContain('0:01–0:05</span> <span class="min-w-0 flex-1">Safety drill<span class="sr-only">: show in the transcript</span>');
    expect(html).toContain("Battery alerts for Speaker 2"); // a label nobody named yet reads as a speaker
    expect(html).toContain('<span class="sr-only">Topic: </span>Safety drill');
    expect(html).not.toContain("Worked out from the transcript");

    const recording = render(
      meeting({
        has_media: true,
        filename: "weekly-ops.m4a",
        topics: [{ title: "Part, coffee, machine", segment: "T1", start: 1, end: 10, source: "words" }],
      }),
    );
    expect(recording).toContain('Part, coffee, machine<span class="sr-only">: play from here</span>');
    expect(recording).toContain("Worked out from the transcript&#x27;s words");
  });

  it("lets the uploader name diarized voices, with what each said first and a guest option", () => {
    const html = render(
      meeting({
        has_media: true,
        filename: "call.m4a",
        summary: "SPEAKER_1 asked SPEAKER_2 for the quote.",
        segments: [
          { id: "T1", start: 1, end: 5, speaker: "SPEAKER_1", text: "Can you send the quote?" },
          { id: "T2", start: 6, end: 10, speaker: "SPEAKER_2", text: "Yes, by Friday." },
        ],
        speakers: {
          SPEAKER_1: { name: "SPEAKER_1", user_id: null },
          SPEAKER_2: { name: "Jonas Brandt", user_id: null, guest: true },
        },
        decisions: [],
        action_items: [],
      }),
    );
    expect(html).toContain("Speaker 1 asked Speaker 2 for the quote.");
    expect(html).toContain("Speaker 1: </span>Can you send the quote?");
    expect(html).toContain("Jonas Brandt: </span>Yes, by Friday.");
    expect(html).toContain(">Who is Speaker 1?</label>");
    expect(html).toContain('aria-label="Listen to Speaker 1 from 0:01"');
    expect(html).toContain("“Can you send the quote?”");
    expect(html).toContain("Guest&#x27;s name for Speaker 2");
    expect(html).toContain('value="Jonas Brandt"');
    expect(html).toContain("Save names");
  });

  it("says why a recording has no speaker labels", () => {
    const html = render(
      meeting({
        has_media: true,
        filename: "call.m4a",
        segments: [{ id: "T1", start: 1, end: 5, speaker: null, text: "Hello." }],
        speakers: {},
        timings: { diarization_skipped: "speaker diarization is off (DIARIZATION_PROVIDER=none)" },
      }),
    );
    expect(html).toContain("Speakers weren&#x27;t told apart: speaker diarization is off (DIARIZATION_PROVIDER=none).");
    expect(html).not.toContain("Who is who");
  });
});
