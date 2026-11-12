/**
 * Typed client for meetings. Shapes mirror apps/api/src/workmind/api/v1/meetings.py and
 * modules/meetings (segments from pipeline.py, decisions and action items from extract.py).
 */

import { apiJson } from "@/lib/api";

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
  created_at: string;
}

export interface Segment {
  /** "T1", "T2", … */
  id: string;
  start: number;
  end: number;
  /** A speaker label from the transcript; null for recordings (no diarization). */
  speaker: string | null;
  text: string;
}

export interface Speaker {
  name: string;
  user_id: string | null;
}

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
  document_id: string | null;
  timings: Record<string, number>;
  can_edit: boolean;
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
  /** The raw file as the body (not multipart). Processing continues in the background. */
  upload: (params: { collection: string; filename: string; title?: string }, file: Blob) =>
    apiJson<MeetingSummary>(uploadPath(params), {
      method: "POST",
      body: file,
      headers: { "Content-Type": "application/octet-stream" },
    }),
  mediaUrl: (meetingId: string) => `/api/v1/meetings/${id(meetingId)}/media`,
  setSpeakers: (meetingId: string, speakers: Record<string, string | null>) =>
    apiJson<Meeting>(`/api/v1/meetings/${id(meetingId)}/speakers`, { method: "PUT", json: { speakers } }),
  editAction: (meetingId: string, index: number, body: ActionEdit) =>
    apiJson<Meeting>(`/api/v1/meetings/${id(meetingId)}/actions/${index}`, { method: "PATCH", json: body }),
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
