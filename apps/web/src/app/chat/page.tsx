import { ChatThread } from "@/components/chat/chat-thread";
import { sectionMetadata } from "@/components/page-placeholder";

export const metadata = sectionMetadata("/chat");

export default function ChatPage() {
  return <ChatThread conversationId={null} />;
}
