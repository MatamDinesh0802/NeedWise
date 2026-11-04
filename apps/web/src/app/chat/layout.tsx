import { ChatShell } from "@/components/chat/chat-shell";

export default function ChatLayout({ children }: LayoutProps<"/chat">) {
  return <ChatShell>{children}</ChatShell>;
}
