/**
 * Meetings, framework-free: which files can be uploaded, where processing is, timestamps and
 * where a click on one should jump, topics, naming speakers, the corrections payload and "My
 * commitments". Tested on its own; components/meetings/* only render it.
 */

import { ApiError } from "@/lib/api";
import { errorMessage, type ErrorMessage } from "@/lib/error-messages";
import {
  meetingsKeys,
  type ActionEdit,
  type ActionItem,
  type Commitment,
  type Decision,
  type Meeting,
  type MeetingStatus,
  type Segment,
  type Speaker,
  type SpeakerChoice,
  type Topic,
} from "@/lib/meetings/api";
import { workKeys } from "@/lib/work/api";
import { dueText } from "@/lib/work/tasks";

// --- Upload -----------------------------------------------------------------------------------

/** Same lists as the API (AUDIO and TEXT in modules/meetings/transcripts.py). */
export const AUDIO_EXTENSIONS = ["mp3", "m4a", "wav", "mp4", "webm", "ogg", "flac", "aiff", "aif", "mov"] as const;
export const TRANSCRIPT_EXTENSIONS = ["vtt", "srt", "txt", "md"] as const;
export const ACCEPT = [...AUDIO_EXTENSIONS, ...TRANSCRIPT_EXTENSIONS].map((e) => `.${e}`).join(",");
/** MAX_MEETING_BYTES default (500 MB). */
export const MAX_MEETING_BYTES = 500 * 1024 * 1024;
export const TITLE_MAX = 200;

export const CONSENT_NOTICE = "Everyone in the recording should know it is being processed.";

export type FileKind = "audio" | "transcript";

/** Like Python's PurePath.suffix: ".mp3" alone or "notes." have none. */
function extension(filename: string): string {
  const name = filename.split(/[\\/]/).pop() ?? "";
  const dot = name.lastIndexOf(".");
  return dot <= 0 ? "" : name.slice(dot + 1).toLowerCase();
}

export function fileKind(filename: string): FileKind | null {
  const ext = extension(filename);
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

/**
 * The title after picking `next`: follows the file name until someone types their own title
 * (it is then theirs, whatever file they pick).
 */
export function titleAfterPick(current: string, previous: string | null, next: string | null): string {
  const untouched = current.trim() === "" || (previous !== null && current === titleFromFilename(previous));
  if (!untouched) return current;
  return next ? titleFromFilename(next) : "";
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

const MESSAGES: Record<string, Omit<ErrorMessage, "code">> = {
  too_large: { title: "The file is too large", description: "Recordings and transcripts can be up to 500 MB." },
  unsupported_type: {
    title: "File type not supported",
    description: "Use a recording (mp3, m4a, wav, mp4, webm, ogg, flac, aiff, mov) or a transcript (vtt, srt, txt, md).",
  },
  empty_file: { title: "The file is empty", description: "Choose the recording or transcript again." },
  already_a_task: {
    title: "This is a task now",
    description: "Once an action item is a task, change the task instead. The page now shows its link.",
  },
  unknown_user: { title: "Unknown colleague", description: "That person isn't an active member any more. Choose someone else." },
  unknown_speaker: { title: "Unknown speaker", description: "The transcript has no speaker by that name. Reload the page." },
  forbidden: {
    title: "You can't change this meeting",
    description:
      "Only the person who uploaded it and editors of its collection can correct items and make tasks. Speakers can be named by the person who uploaded it, attendees who can edit it, and admins.",
  },
  not_ready: { title: "Still processing", description: "Name the speakers once the notes are ready." },
  empty_name: { title: "The guest needs a name", description: "Type the guest's name, or choose a colleague." },
};

/** Meeting API errors in words (falls back to the app-wide messages). */
export function meetingErrorMessage(error: unknown): ErrorMessage {
  if (error instanceof ApiError) {
    const code = error.code ?? (error.status === 413 ? "too_large" : error.status === 415 ? "unsupported_type" : null);
    if (code && Object.hasOwn(MESSAGES, code)) return { ...MESSAGES[code], code };
  }
  return errorMessage(error);
}

// --- Deleting ---------------------------------------------------------------------------------

/** What deleting removes and what stays, for the confirmation (`tasks`: action items made tasks). */
export function meetingDeletionText(tasks: number): string {
  const kept =
    tasks === 0
      ? ""
      : tasks === 1
        ? " The task made from one of its action items stays with its owner."
        : ` The ${tasks} tasks made from its action items stay with their owners.`;
  return `The meeting, its recording or transcript file, and its searchable transcript are removed for everyone who can see this collection.${kept} This can't be undone.`;
}

/** Lists a deleted meeting leaves stale: the meetings list, "My commitments", and task lists. */
export const MEETING_DELETION_KEYS: readonly string[] = [meetingsKeys.list, meetingsKeys.commitments, workKeys.tasks];

/** What happens next, for the toast after an upload. */
export function uploadedText(kind: FileKind | null): string {
  return kind === "audio"
    ? "Transcribing the recording now. The list updates as it goes, and you'll get a notification when the notes are ready."
    : "Reading the transcript now. You'll get a notification when the notes are ready.";
}

// --- Status -----------------------------------------------------------------------------------

export const STATUS_LABELS: Record<MeetingStatus, string> = {
  pending: "Queued",
  transcribing: "Transcribing",
  extracting: "Extracting",
  ready: "Ready",
  failed: "Failed",
};

export const PIPELINE: readonly MeetingStatus[] = ["pending", "transcribing", "extracting", "ready"];

export function isProcessing(status: MeetingStatus): boolean {
  return status === "pending" || status === "transcribing" || status === "extracting";
}

/** One line per pipeline step, worded for a recording or a transcript. */
export function stepLabel(status: MeetingStatus, hasMedia: boolean): string {
  switch (status) {
    case "pending":
      return "Queued";
    case "transcribing":
      return hasMedia ? "Transcribing the recording" : "Reading the transcript";
    case "extracting":
      return "Finding the summary, decisions and action items";
    case "ready":
      return "Notes ready";
    case "failed":
      return "Failed";
  }
}

/** Steps for a progress list: each is done, current or still to come. */
export function pipelineSteps(status: MeetingStatus): { status: MeetingStatus; state: "done" | "current" | "todo" }[] {
  if (status === "failed") return PIPELINE.map((s) => ({ status: s, state: "todo" as const }));
  const at = PIPELINE.indexOf(status);
  return PIPELINE.map((s, i) => ({ status: s, state: i < at || status === "ready" ? "done" : i === at ? "current" : "todo" }));
}

// --- Dates and timestamps ---------------------------------------------------------------------

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

const DAY = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "UTC" });

/**
 * A calendar day ("2026-11-04") as the viewer reads dates. Formatted in UTC: `new Date("2026-11-04")`
 * is midnight UTC, which local formatting west of UTC shows as the 3rd.
 */
export function formatDay(day: string | null): string {
  if (!day || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return "—";
  const time = Date.parse(`${day}T00:00:00Z`);
  return Number.isNaN(time) ? "—" : DAY.format(time);
}

/** "Due tomorrow" for an action item's YYYY-MM-DD (tasks fall due at 17:00 UTC, like the API's). */
export function dueOf(day: string | null, now?: Date): { text: string; overdue: boolean } | null {
  return day ? dueText(`${day}T17:00:00Z`, now) : null;
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

/** How many decisions and action items have no timestamp (shown as a note, not a guess). */
export function untimedCount(meeting: Pick<Meeting, "decisions" | "action_items">): number {
  return [...meeting.decisions, ...meeting.action_items].filter((item) => item.segment == null || item.start == null).length;
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

/**
 * Where to scroll the transcript panel (its own scrollTop) to show a segment, without moving the
 * page: "center" for a jump, "nearest" to follow playback (null: it is already in view).
 * Positions are relative to the panel's top edge, as getBoundingClientRect gives them.
 */
export function panelScrollTop(
  { top, height }: { top: number; height: number },
  panel: { scrollTop: number; clientHeight: number },
  block: "center" | "nearest",
): number | null {
  const offset = top + panel.scrollTop;
  if (block === "center") return Math.max(0, Math.round(offset - (panel.clientHeight - height) / 2));
  if (top < 0) return Math.max(0, Math.round(offset));
  if (top + height > panel.clientHeight) return Math.max(0, Math.round(offset - panel.clientHeight + height));
  return null;
}

/** "?t=271.4" from a commitment link → 271.4; anything else → null. */
export function parseStartParam(value: string | null): number | null {
  if (!value || !/^\d+(\.\d+)?$/.test(value)) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/**
 * A link to the meeting at a moment. Keeps the start as the API stores it (two decimals): rounding
 * 271.4 down to 271 would land in the segment before it.
 */
export function meetingLink(meetingId: string, start: number | null): `/meetings/${string}` {
  const id = encodeURIComponent(meetingId);
  return start != null && Number.isFinite(start) && start >= 0 ? `/meetings/${id}?t=${Math.round(start * 100) / 100}` : `/meetings/${id}`;
}

// --- Topics -----------------------------------------------------------------------------------

/** The topic being discussed at `time` (topics are in order): its index, or null before the first. */
export function topicAt(topics: readonly Pick<Topic, "start">[], time: number): number | null {
  let found: number | null = null;
  for (let index = 0; index < topics.length && topics[index].start <= time; index++) found = index;
  return found;
}

/** "4:31–9:02". */
export function topicRange(topic: Pick<Topic, "start" | "end">): string {
  return `${formatTimestamp(topic.start)}–${formatTimestamp(topic.end)}`;
}

/** Where the outline came from, when it is not the model's (null when it is). */
export function topicsNote(topics: readonly Pick<Topic, "source">[]): string | null {
  return topics.some((t) => t.source === "words")
    ? "Worked out from the transcript's words, because the model's outline couldn't be used: each title is the words that set that stretch apart."
    : null;
}

/** Topic by the segment it starts at, for headings in the transcript. */
export function topicStarts<T extends Pick<Topic, "segment">>(topics: readonly T[]): Map<string, T> {
  return new Map(topics.map((topic) => [topic.segment, topic]));
}

// --- Speakers and owners ----------------------------------------------------------------------

const DIARIZED = /\bSPEAKER_(\d+)\b/g;

/**
 * Labels from diarization as people read them: "SPEAKER_3" is "Speaker 3", in a label or in text
 * the model wrote with one nobody has named yet ("SPEAKER_2 will…"). Names stay as they are.
 */
export function labelText(text: string): string {
  return text.replace(DIARIZED, "Speaker $1");
}

export function speakerName(label: string | null, speakers: Readonly<Record<string, Speaker>>): string {
  if (!label) return "Speaker";
  const name = speakers[label]?.name;
  return name && name !== label ? name : labelText(label);
}

export interface SpeakerOverview {
  label: string;
  segments: number;
  seconds: number;
  /** The first thing they said: something to recognise them by. */
  first: Segment | null;
}

/** Each speaker label with how much they said and where they first spoke, in order of appearance. */
export function speakerOverview(segments: readonly Segment[], speakers: Readonly<Record<string, Speaker>>): SpeakerOverview[] {
  const seen = new Map<string, SpeakerOverview>();
  for (const segment of segments) {
    if (!segment.speaker || !(segment.speaker in speakers)) continue;
    const entry = seen.get(segment.speaker) ?? { label: segment.speaker, segments: 0, seconds: 0, first: segment };
    entry.segments += 1;
    entry.seconds += Math.max(0, segment.end - segment.start);
    seen.set(segment.speaker, entry);
  }
  const silent = Object.keys(speakers)
    .filter((label) => !seen.has(label))
    .sort((a, b) => a.localeCompare(b))
    .map((label) => ({ label, segments: 0, seconds: 0, first: null }));
  return [...seen.values(), ...silent];
}

/** Why a recording's transcript has no speaker labels (null when it has them, or is a transcript). */
export function speakerNote(meeting: Pick<Meeting, "speakers" | "timings" | "filename">): string | null {
  if (fileKind(meeting.filename) !== "audio" || Object.keys(meeting.speakers).length > 0) return null;
  const reason = meeting.timings.diarization_skipped;
  return reason ? `Speakers weren't told apart: ${reason}.` : "Speakers weren't told apart in this recording.";
}

/** What "Who is who" holds for one label while it is being edited. */
export type SpeakerDraft = { kind: "nobody" } | { kind: "person"; id: string } | { kind: "guest"; name: string };

/** The select's value for a guest (any other value is a colleague's id, or "" for nobody). */
export const GUEST = "__guest__";
export const GUEST_NAME_MAX = 100;

export function speakerDraft(speaker: Speaker | undefined): SpeakerDraft {
  if (speaker?.user_id) return { kind: "person", id: speaker.user_id };
  if (speaker?.guest) return { kind: "guest", name: speaker.name };
  return { kind: "nobody" };
}

export function draftValue(draft: SpeakerDraft): string {
  return draft.kind === "person" ? draft.id : draft.kind === "guest" ? GUEST : "";
}

/** The draft after picking `value` in the select (a guest keeps the name typed so far). */
export function draftFromValue(value: string, previous: SpeakerDraft): SpeakerDraft {
  if (value === GUEST) return { kind: "guest", name: previous.kind === "guest" ? previous.name : "" };
  return value ? { kind: "person", id: value } : { kind: "nobody" };
}

/** Options for naming a speaker: nobody, a guest, then colleagues (the current one is kept). */
export function speakerOptions(people: readonly PersonOption[], current: Speaker | undefined): { value: string; label: string }[] {
  const colleagues = personOptions(people, current?.user_id ? { id: current.user_id, name: current.name } : null, "Not named yet");
  return [colleagues[0], { value: GUEST, label: "A guest (type their name)" }, ...colleagues.slice(1)];
}

/**
 * The mapping as the API takes it: only labels whose name changed, a colleague as their id, a
 * guest as `{ name }`, and null for "not named". A guest needs a name.
 */
export function speakerMapPayload(
  current: Readonly<Record<string, Speaker>>,
  drafts: Readonly<Record<string, SpeakerDraft>>,
): { ok: true; body: Record<string, SpeakerChoice> } | { ok: false; errors: Record<string, string> } {
  const body: Record<string, SpeakerChoice> = {};
  const errors: Record<string, string> = {};
  for (const [label, draft] of Object.entries(drafts)) {
    const now = current[label];
    if (!now) continue;
    if (draft.kind === "person") {
      if (now.user_id !== draft.id) body[label] = draft.id;
    } else if (draft.kind === "guest") {
      const name = draft.name.trim().replace(/\s+/g, " ");
      if (!name) errors[label] = "Type the guest's name.";
      else if (name.length > GUEST_NAME_MAX) errors[label] = `At most ${GUEST_NAME_MAX} characters.`;
      else if (!(now.guest && now.name === name)) body[label] = { name };
    } else if (now.user_id || now.guest) {
      body[label] = null;
    }
  }
  return Object.keys(errors).length > 0 ? { ok: false, errors } : { ok: true, body };
}

export interface PersonOption {
  id: string;
  name: string;
}

/**
 * People this meeting already linked (speakers and owners) plus the viewer: what an editor can
 * pick when the colleague directory isn't available. Unique by id, sorted by name.
 */
export function meetingPeople(meeting: Pick<Meeting, "speakers" | "action_items">, me: PersonOption): PersonOption[] {
  const people = new Map<string, string>([[me.id, me.name]]);
  for (const speaker of Object.values(meeting.speakers)) if (speaker.user_id) people.set(speaker.user_id, speaker.name);
  for (const item of meeting.action_items) if (item.owner_user_id && !people.has(item.owner_user_id)) people.set(item.owner_user_id, item.owner);
  return [...people].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
}

/** `base` plus the directory, unique by id (the directory's name wins), sorted by name. */
export function mergePeople(base: readonly PersonOption[], directory: readonly { id: string; display_name: string }[]): PersonOption[] {
  const all = new Map(base.map((p) => [p.id, p.name]));
  for (const person of directory) all.set(person.id, person.display_name);
  return [...all].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
}

/**
 * Options for choosing a colleague: "nobody" first, then people; the current choice is kept even
 * when it isn't in the list (a select must never lose its own value).
 */
export function personOptions(
  people: readonly PersonOption[],
  current: PersonOption | null,
  noneLabel: string,
): { value: string; label: string }[] {
  const options = [{ value: "", label: noneLabel }, ...people.map((p) => ({ value: p.id, label: p.name }))];
  if (current?.id && !people.some((p) => p.id === current.id)) options.push({ value: current.id, label: current.name || "Current choice" });
  return options;
}

export function ownerText(item: Pick<ActionItem, "owner" | "owner_user_id">, meId: string | null): string {
  if (item.owner_user_id && item.owner_user_id === meId) return "You";
  return labelText(item.owner?.trim() || "") || "Nobody yet";
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

// --- My commitments ---------------------------------------------------------------------------

export const TASK_STATUS_TEXT: Record<string, string> = {
  open: "Open task",
  in_progress: "In progress",
  done: "Done",
  cancelled: "Cancelled",
};

/** The badge for a commitment: its task's status, or that it isn't a task yet. */
export function commitmentStatus(c: Pick<Commitment, "task_id" | "task_status">): string {
  if (!c.task_id) return "Not a task yet";
  return TASK_STATUS_TEXT[c.task_status ?? ""] ?? "Task";
}

export function isCommitmentClosed(c: Pick<Commitment, "task_status">): boolean {
  return c.task_status === "done" || c.task_status === "cancelled";
}

/** Open commitments first (soonest due first, no deadline last), finished ones at the end. */
export function sortCommitments(items: readonly Commitment[]): Commitment[] {
  const closed = (c: Commitment) => (isCommitmentClosed(c) ? 1 : 0);
  const due = (c: Commitment) => (c.due ? Date.parse(`${c.due}T17:00:00Z`) : Number.POSITIVE_INFINITY);
  const order = (a: Commitment, b: Commitment) => closed(a) - closed(b) || due(a) - due(b);
  return [...items].sort((a, b) => order(a, b) || b.meeting_date.localeCompare(a.meeting_date) || a.index - b.index);
}
