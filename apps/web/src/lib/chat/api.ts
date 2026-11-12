/**
 * Typed client for conversations and runs. Shapes mirror
 * apps/api/src/workmind/api/v1/conversations.py.
 */

import { apiJson } from "@/lib/api";
import type { ChatMode } from "@/lib/chat/research";
import type { Sensitivity, Trust } from "@/lib/knowledge/api";

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  last_message_at: string;
}

export interface Citation {
  marker: string;
  document_id: string;
  title: string;
  filename?: string;
  section: string | null;
  page: number | null;
  trust: Trust;
}

/** A retrieved passage. `text` only arrives live (retrieval event), never from history. */
export interface Source {
  marker: string;
  document_id: string;
  chunk_id: string;
  collection_id: string;
  title: string;
  filename: string;
  section: string | null;
  page: number | null;
  trust: Trust;
  sensitivity: Sensitivity;
  text?: string;
}

export interface Usage {
  model?: string;
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number | null;
}

export type MessageStatus = "complete" | "cancelled" | "failed";

/** One tool call of a research answer, stored with it (names and outcomes, not the content). */
export interface StoredToolStep {
  tool: string;
  ok: boolean;
  error: string | null;
  /** Set when the call became an approval. */
  approval_id: string | null;
  summary: string | null;
}
export type Rating = "up" | "down";

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: MessageStatus;
  created_at: string;
  run_id: string | null;
  search_query: string | null;
  citations: Citation[];
  sources: Source[];
  /** Research answers only; [] otherwise (and missing from older API versions). */
  tool_steps?: StoredToolStep[];
  usage: Usage;
  abstained: boolean;
  withheld: number;
  feedback: Rating | null;
  feedback_comment: string | null;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
  active_run_id: string | null;
}

export interface ConversationPage {
  items: Conversation[];
  next_cursor: string | null;
}

export interface RunStarted {
  run_id: string;
  user_message: Message;
}

const id = (value: string) => encodeURIComponent(value);

export const chatApi = {
  listConversations: (cursor: string | null, limit = 30) =>
    apiJson<ConversationPage>(
      `/api/v1/conversations?limit=${limit}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
    ),
  createConversation: (title = "") =>
    apiJson<Conversation>("/api/v1/conversations", { method: "POST", json: { title } }),
  getConversation: (conversationId: string) =>
    apiJson<ConversationDetail>(`/api/v1/conversations/${id(conversationId)}`),
  renameConversation: (conversationId: string, title: string) =>
    apiJson<Conversation>(`/api/v1/conversations/${id(conversationId)}`, { method: "PATCH", json: { title } }),
  deleteConversation: (conversationId: string) =>
    apiJson<void>(`/api/v1/conversations/${id(conversationId)}`, { method: "DELETE" }),
  /** "research": the research agent may use tools and propose actions for approval. */
  sendMessage: (conversationId: string, content: string, mode: ChatMode = "answer") =>
    apiJson<RunStarted>(`/api/v1/conversations/${id(conversationId)}/messages`, {
      method: "POST",
      json: mode === "research" ? { content, mode } : { content },
    }),
  cancelRun: (runId: string) => apiJson<void>(`/api/v1/runs/${id(runId)}/cancel`, { method: "POST" }),
  feedback: (messageId: string, rating: Rating | null, comment?: string | null) =>
    apiJson<Message>(`/api/v1/messages/${id(messageId)}/feedback`, {
      method: "POST",
      json: { rating, comment: comment ?? null },
    }),
  /** SSE stream; EventSource sends the cookie and resumes with Last-Event-ID by itself. */
  eventsUrl: (runId: string) => `/api/v1/runs/${id(runId)}/events`,
};

export const chatKeys = {
  conversation: (conversationId: string) => `chat/conversations/${conversationId}`,
};

/** Opens a PDF in the browser at a page (the API only serves PDFs inline). */
export function pdfPageUrl(documentId: string, page: number): string {
  return `/api/v1/documents/${id(documentId)}/file?inline=true#page=${page}`;
}
