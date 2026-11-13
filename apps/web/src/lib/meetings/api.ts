/**
 * Typed client for meetings. Shapes mirror apps/api/src/needwise/api/v1/meetings.py and
 * modules/meetings (segments from pipeline.py, decisions and action items from extract.py,
 * topics from topics.py, speaker labels from speakers.py).
 */

import { apiJson, uploadRaw, type UploadOptions } from "@/lib/api";

export type MeetingStatus = "pending" | "transcribing" | "extracting" | "ready" | "failed";

export interface MeetingSummary {
  id: string;
  title: string;
  collection_id: string;
  status: MeetingStatus;
  error: string | null;
  duration_s: number | null;
  decisions: number;
  action_items: number;
  /** YYYY-MM-DD: the day of the meeting (a date in its file name), else of the upload. */
  held_on: string;
  created_at: string;
}

export interface Segment {
  /** "T1", "T2", … */
  id: string;
  start: number;
  end: number;
  /**
   * A speaker label: a name the transcript gave, or SPEAKER_1, SPEAKER_2, … from diarization
   * (who spoke when in a recording). Null when nobody was told apart.
   */
  speaker: string | null;
  text: string;
}

export interface Speaker {
  /** The colleague's or guest's name, or the label itself while nobody is named. */
  name: string;
  user_id: string | null;
  /** A name typed in "Who is who": someone outside the directory, linked to nobody. */
  guest?: boolean;
}

/** One stretch of the meeting about one thing, with the transcript's own times. */
export interface Topic {
  title: string;
  /** The segment it starts at. */
  segment: string;
  start: number;
  end: number;
  /** "model": the model's outline, checked against the transcript. "words": worked out from the transcript's words, because the model's could not be used. */
  source: "model" | "words";
}

/** How long each step took, and what happened to the optional ones. */
export interface MeetingTimings {
  transcribe_s?: number;
  diarize_s?: number;
  extract_s?: number;
  topics_s?: number;
  model?: string;
  diarizer?: string;
  speakers_found?: number;
  /** Why a recording has no speaker labels (diarization off, not installed, or failed). */
  diarization_skipped?: string;
  /** Why the topics come from the transcript's words rather than the model. */
  topics_fallback?: string;
}

/** Naming a speaker: a colleague's id, a guest's name, or null to leave the label unnamed. */
export type SpeakerChoice = string | { name: string } | null;

/** `segment`/`start` are null when the model pointed at a segment that doesn't exist. */
export interface Decision {
  text: string;
  segment: string | null;
  start: number | null;
}

export interface ActionItem {
  text: string;
  /** The name as said in the meeting (or the corrected owner's name). */
  owner: string;
  owner_user_id: string | null;
  /** YYYY-MM-DD or null. */
  due: string | null;
  task_id: string | null;
  segment: string | null;
  start: number | null;
}

export interface Meeting {
  id: string;
  title: string;
  collection_id: string;
  owner_id: string;
  status: MeetingStatus;
  error: string | null;
  filename: string;
  has_media: boolean;
  duration_s: number | null;
  language: string | null;
  segments: Segment[];
  speakers: Record<string, Speaker>;
  summary: string;
  decisions: Decision[];
  action_items: ActionItem[];
  topics: Topic[];
  document_id: string | null;
  timings: MeetingTimings;
  can_edit: boolean;
  /** The uploader, attendees (a speaker named as them) who can edit, and admins. */
  can_map_speakers: boolean;
  /** YYYY-MM-DD: the day of the meeting (a date in its file name), else of the upload. */
  held_on: string;
  created_at: string;
}

export interface ActionEdit {
  text?: string;
  /** null: nobody. */
  owner_user_id?: string | null;
  /** YYYY-MM-DD, or null for no deadline. */
  due?: string | null;
}

export interface Commitment {
  meeting_id: string;
  meeting_title: string;
  meeting_date: string;
  index: number;
  text: string;
  due: string | null;
  start: number | null;
  task_id: string | null;
  task_status: string | null;
}

const id = (value: string) => encodeURIComponent(value);

export function uploadPath({ collection, filename, title }: { collection: string; filename: string; title?: string }): string {
  const params = new URLSearchParams({ collection, filename });
  if (title?.trim()) params.set("title", title.trim());
  return `/api/v1/meetings?${params.toString()}`;
}

export const meetingsApi = {
  list: () => apiJson<MeetingSummary[]>("/api/v1/meetings"),
  get: (meetingId: string) => apiJson<Meeting>(`/api/v1/meetings/${id(meetingId)}`),
  /**
   * The raw file as the body (not multipart), with upload progress: recordings go up to 500 MB.
   * 202: processing continues in the background.
   */
  upload: (params: { collection: string; filename: string; title?: string }, file: Blob, options?: UploadOptions) =>
    uploadRaw<MeetingSummary>(uploadPath(params), file, "application/octet-stream", options),
  mediaUrl: (meetingId: string) => `/api/v1/meetings/${id(meetingId)}/media`,
  /** Only the uploader, attendees who can edit, and admins (`can_map_speakers`). */
  setSpeakers: (meetingId: string, speakers: Record<string, SpeakerChoice>) =>
    apiJson<Meeting>(`/api/v1/meetings/${id(meetingId)}/speakers`, { method: "PUT", json: { speakers } }),
  editAction: (meetingId: string, index: number, body: ActionEdit) =>
    apiJson<Meeting>(`/api/v1/meetings/${id(meetingId)}/actions/${index}`, { method: "PATCH", json: body }),
  /** The uploader or the collection's editors: the meeting, its file and its indexed transcript. */
  remove: (meetingId: string) => apiJson<void>(`/api/v1/meetings/${id(meetingId)}`, { method: "DELETE" }),
  makeTask: (meetingId: string, index: number) =>
    apiJson<Meeting>(`/api/v1/meetings/${id(meetingId)}/actions/${index}/task`, { method: "POST" }),
  commitments: () => apiJson<Commitment[]>("/api/v1/me/commitments"),
};

export const meetingsKeys = {
  all: "meetings",
  list: "meetings/list",
  meeting: (meetingId: string) => `meetings/one/${meetingId}`,
  commitments: "meetings/commitments",
};
