/**
 * Meetings, framework-free: which files can be uploaded, where processing is, timestamps and
 * where a click on one should jump, and the corrections payload. Tested on its own.
 */

import type { ActionEdit, ActionItem, Decision, Meeting, MeetingStatus, Segment, Speaker } from "@/lib/meetings/api";

// --- Upload -----------------------------------------------------------------------------------

/** Same lists as the API (modules/meetings/transcripts.py, MEDIA_TYPES in api/v1/meetings.py). */
export const AUDIO_EXTENSIONS = ["mp3", "m4a", "wav", "mp4", "webm", "ogg", "flac", "aiff", "aif", "mov"] as const;
export const TRANSCRIPT_EXTENSIONS = ["vtt", "srt", "txt", "md"] as const;
export const ACCEPT = [...AUDIO_EXTENSIONS, ...TRANSCRIPT_EXTENSIONS].map((e) => `.${e}`).join(",");
/** MAX_MEETING_BYTES default (500 MB). */
export const MAX_MEETING_BYTES = 500 * 1024 * 1024;

export type FileKind = "audio" | "transcript";

export function fileKind(filename: string): FileKind | null {
  const ext = filename.toLowerCase().split(".").pop() ?? "";
  if (!filename.includes(".")) return null;
  if ((AUDIO_EXTENSIONS as readonly string[]).includes(ext)) return "audio";
  if ((TRANSCRIPT_EXTENSIONS as readonly string[]).includes(ext)) return "transcript";
  return null;
}

/** "weekly-ops-sync-2026-11-02.m4a" → "Weekly ops sync 2026-11-02". */
export function titleFromFilename(filename: string): string {
  const stem = filename.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  const restored = stem.replace(/(\d{4}) (\d{2}) (\d{2})/g, "$1-$2-$3");
  return restored ? restored.charAt(0).toUpperCase() + restored.slice(1) : "";
}

export interface UploadCheck {
  file?: string;
  collection?: string;
  consent?: string;
}

export function validateUpload({
  file,
  collection,
  consent,
}: {
  file: { name: string; size: number } | null;
  collection: string;
  consent: boolean;
}): UploadCheck {
  const errors: UploadCheck = {};
  if (!file) errors.file = "Choose a recording or a transcript.";
  else if (!fileKind(file.name)) errors.file = "Use a recording (mp3, m4a, wav, mp4, webm, ogg, flac, aiff, mov) or a transcript (vtt, srt, txt, md).";
  else if (file.size === 0) errors.file = "The file is empty.";
  else if (file.size > MAX_MEETING_BYTES) errors.file = "Recordings can be up to 500 MB.";
  if (!collection) errors.collection = "Choose where the meeting goes.";
  if (!consent) errors.consent = "Confirm that everyone in the meeting knows it is being processed.";
  return errors;
}

// --- Status -----------------------------------------------------------------------------------

export const STATUS_LABELS: Record<MeetingStatus, string> = {
  pending: "Queued",
  transcribing: "Transcribing…",
  extracting: "Finding decisions and action items…",
  ready: "Ready",
  failed: "Failed",
};

export const PIPELINE: readonly MeetingStatus[] = ["pending", "transcribing", "extracting", "ready"];

export function isProcessing(status: MeetingStatus): boolean {
  return status === "pending" || status === "transcribing" || status === "extracting";
}

/** Steps for a progress line: each is done, current or still to come. */
export function pipelineSteps(status: MeetingStatus): { status: MeetingStatus; state: "done" | "current" | "todo" }[] {
  if (status === "failed") return PIPELINE.map((s) => ({ status: s, state: "todo" as const }));
  const at = PIPELINE.indexOf(status);
  return PIPELINE.map((s, i) => ({ status: s, state: i < at || status === "ready" ? "done" : i === at ? "current" : "todo" }));
}

// --- Timestamps -------------------------------------------------------------------------------

/** 271.4 → "4:31", 3723 → "1:02:03". */
export function formatTimestamp(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

/** 1385 → "23 min", 45 → "1 min", 3900 → "1 h 5 min". */
export function formatDuration(seconds: number | null): string | null {
  if (seconds == null || !Number.isFinite(seconds)) return null;
  const minutes = Math.max(1, Math.round(seconds / 60));
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${h} h ${rest} min` : `${h} h`;
}

export type JumpTarget = { kind: "media"; seconds: number; segment: string | null } | { kind: "segment"; segment: string };

/**
 * Where a click on a decision or action item goes: the recording at that moment, or (for a
 * transcript upload) the segment in the transcript. Items without a valid segment get no
 * timestamp at all: the API never makes one up, and neither do we.
 */
export function jumpTarget(item: Pick<Decision, "segment" | "start">, hasMedia: boolean): JumpTarget | null {
  if (item.segment == null || item.start == null) return null;
  return hasMedia ? { kind: "media", seconds: item.start, segment: item.segment } : { kind: "segment", segment: item.segment };
}

/** The segment playing at `time` (segments are in order; gaps keep the previous one). */
export function activeSegment(segments: readonly Segment[], time: number): string | null {
  let lo = 0;
  let hi = segments.length - 1;
  let found: string | null = null;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (segments[mid].start <= time) {
      found = segments[mid].id;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}

/** "?t=271" from a commitment link → 271; anything else → null. */
export function parseStartParam(value: string | null): number | null {
  if (!value || !/^\d+(\.\d+)?$/.test(value)) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function meetingLink(meetingId: string, start: number | null): `/meetings/${string}` {
  return start != null ? `/meetings/${meetingId}?t=${Math.floor(start)}` : `/meetings/${meetingId}`;
}

// --- Speakers and owners ----------------------------------------------------------------------

export function speakerName(label: string | null, speakers: Readonly<Record<string, Speaker>>): string {
  if (!label) return "Speaker";
  return speakers[label]?.name || label;
}

export interface PersonOption {
  id: string;
  name: string;
}

/**
 * People to choose from when someone can't use the admin directory: the speakers and owners this
 * meeting already linked to colleagues, plus the viewer. Unique by id, sorted by name.
 */
export function meetingPeople(meeting: Pick<Meeting, "speakers" | "action_items">, me: PersonOption): PersonOption[] {
  const people = new Map<string, string>([[me.id, me.name]]);
  for (const speaker of Object.values(meeting.speakers)) if (speaker.user_id) people.set(speaker.user_id, speaker.name);
  for (const item of meeting.action_items) if (item.owner_user_id && !people.has(item.owner_user_id)) people.set(item.owner_user_id, item.owner);
  return [...people].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
}

/** The speaker mapping as the API takes it: only labels whose colleague changed. */
export function speakerChanges(
  current: Readonly<Record<string, Speaker>>,
  chosen: Readonly<Record<string, string | null>>,
): Record<string, string | null> {
  const out: Record<string, string | null> = {};
  for (const [label, userId] of Object.entries(chosen)) {
    if (label in current && (current[label].user_id ?? null) !== userId) out[label] = userId;
  }
  return out;
}

export function ownerText(item: Pick<ActionItem, "owner" | "owner_user_id">, meId: string | null): string {
  if (item.owner_user_id && item.owner_user_id === meId) return "You";
  return item.owner?.trim() || "Nobody yet";
}

// --- Correcting an action item ----------------------------------------------------------------

export interface ActionForm {
  text: string;
  ownerUserId: string;
  due: string;
}

export function actionForm(item: ActionItem): ActionForm {
  return { text: item.text, ownerUserId: item.owner_user_id ?? "", due: item.due ?? "" };
}

/** Only what changed (the API leaves other fields as they are); null when nothing did. */
export function actionEditPayload(item: ActionItem, form: ActionForm): { ok: true; body: ActionEdit | null } | { ok: false; errors: Partial<Record<keyof ActionForm, string>> } {
  const text = form.text.trim();
  const errors: Partial<Record<keyof ActionForm, string>> = {};
  if (!text) errors.text = "Describe what needs to happen.";
  else if (text.length > 500) errors.text = "At most 500 characters.";
  if (form.due && !/^\d{4}-\d{2}-\d{2}$/.test(form.due)) errors.due = "Pick a date.";
  if (Object.keys(errors).length > 0) return { ok: false, errors };
  const body: ActionEdit = {};
  if (text !== item.text) body.text = text;
  if ((form.ownerUserId || null) !== (item.owner_user_id ?? null)) body.owner_user_id = form.ownerUserId || null;
  if ((form.due || null) !== (item.due ?? null)) body.due = form.due || null;
  return { ok: true, body: Object.keys(body).length > 0 ? body : null };
}

export const TASK_STATUS_TEXT: Record<string, string> = {
  open: "Open task",
  in_progress: "In progress",
  done: "Done",
  cancelled: "Cancelled",
};
