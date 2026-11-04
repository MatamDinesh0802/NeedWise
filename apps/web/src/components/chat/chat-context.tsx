"use client";

import { createContext, useContext } from "react";

import type { Conversation } from "@/lib/chat/api";
import type { ChatMode } from "@/lib/chat/research";
import type { PagedList } from "@/lib/stores/paged-list";

export interface ChatShellContext {
  conversations: PagedList<Conversation>;
  /** Re-read the first page (titles and order change as people ask). */
  refreshConversations: () => void;
  /** "Answer" or "Research & act". Kept here so it survives /chat → /chat/[id] after the first question. */
  mode: ChatMode;
  setMode: (mode: ChatMode) => void;
}

export const ChatContext = createContext<ChatShellContext | null>(null);

export function useChatShell(): ChatShellContext {
  const context = useContext(ChatContext);
  if (!context) throw new Error("useChatShell must be used inside ChatShell");
  return context;
}
