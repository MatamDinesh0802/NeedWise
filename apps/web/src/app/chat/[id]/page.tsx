import type { Metadata } from "next";
import { Suspense } from "react";

import { ChatThreadRoute } from "@/components/chat/chat-thread";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Conversation · Assistant" };

// The id is runtime data: read with useParams behind Suspense so the shell stays static.
export default function ConversationPage() {
  return (
    <Suspense fallback={<Skeleton className="m-8 h-24 w-full max-w-3xl" />}>
      <ChatThreadRoute />
    </Suspense>
  );
}
