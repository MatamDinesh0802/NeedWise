"use client";

import { Ellipsis, History, MessageSquareText, Plus } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Suspense, useState, type ReactNode } from "react";

import { SignedInGate } from "@/components/auth/signed-in-gate";
import { ChatContext, useChatShell } from "@/components/chat/chat-context";
import { ConfirmDialog } from "@/components/data/confirm-dialog";
import { ErrorAlert } from "@/components/data/states";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useStore } from "@/hooks/use-store";
import { chatApi, type Conversation } from "@/lib/chat/api";
import type { ChatMode } from "@/lib/chat/research";
import { formatRelative } from "@/lib/format";
import { createPagedList } from "@/lib/stores/paged-list";
import { cn } from "@/lib/utils";

/** /chat frame: conversation history on the left, the thread on the right. */
export function ChatShell({ children }: { children: ReactNode }) {
  return (
    // Fill the area under the header; cancel the page padding of the root layout.
    <div className="-m-4 flex h-[calc(100svh-3.5rem)] min-h-0 md:-m-6">
      <SignedInGate purpose="ask questions">{() => <ChatFrame>{children}</ChatFrame>}</SignedInGate>
    </div>
  );
}

function ChatFrame({ children }: { children: ReactNode }) {
  const [conversations] = useState(() =>
    createPagedList<Conversation, string>(async (cursor) => {
      const page = await chatApi.listConversations(cursor);
      return { items: page.items, next: page.next_cursor };
    }),
  );
  const [historyOpen, setHistoryOpen] = useState(false);
  const [mode, setMode] = useState<ChatMode>("answer");
  const context = { conversations, refreshConversations: () => void conversations.refresh(), mode, setMode };

  return (
    <ChatContext.Provider value={context}>
      <div className="hidden w-64 shrink-0 flex-col border-r bg-sidebar/40 md:flex">
        <HistoryList />
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b px-4 py-2 md:hidden">
          <Button variant="outline" size="sm" onClick={() => setHistoryOpen(true)}>
            <History data-icon="inline-start" />
            History
          </Button>
          <Link href="/chat" className={buttonVariants({ variant: "ghost", size: "sm" })}>
            <Plus data-icon="inline-start" />
            New chat
          </Link>
        </div>
        {children}
      </div>
      <Sheet open={historyOpen} onOpenChange={setHistoryOpen}>
        <SheetContent side="left" className="w-72 p-0">
          <SheetTitle className="sr-only">Conversation history</SheetTitle>
          <div className="flex h-full flex-col" onClick={(e) => (e.target as HTMLElement).closest("a") && setHistoryOpen(false)}>
            <HistoryList />
          </div>
        </SheetContent>
      </Sheet>
    </ChatContext.Provider>
  );
}

function HistoryList() {
  return (
    <>
      <div className="flex items-center justify-between gap-2 border-b px-3 py-2.5">
        <h2 className="text-sm font-semibold">Conversations</h2>
        <Link href="/chat" className={buttonVariants({ variant: "outline", size: "xs" })} aria-label="New chat">
          <Plus data-icon="inline-start" />
          New
        </Link>
      </div>
      {/* The active id is runtime data on /chat/[id]. */}
      <Suspense fallback={<ConversationItems activeId={null} />}>
        <ActiveConversationItems />
      </Suspense>
    </>
  );
}

function ActiveConversationItems() {
  const params = useParams<{ id?: string }>();
  return <ConversationItems activeId={params?.id ?? null} />;
}

function ConversationItems({ activeId }: { activeId: string | null }) {
  const { conversations } = useChatShell();
  const state = useStore(conversations);
  const router = useRouter();
  const [renameTarget, setRenameTarget] = useState<Conversation | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Conversation | null>(null);

  return (
    <nav aria-label="Conversations" className="flex-1 overflow-y-auto p-2">
      {state.status === "loading" ? (
        <div className="flex flex-col gap-1.5" aria-busy="true">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
      ) : state.status === "error" ? (
        <ErrorAlert error={state.error} onRetry={() => void conversations.reload()} />
      ) : state.items.length === 0 ? (
        <p className="flex flex-col items-center gap-2 px-2 py-8 text-center text-xs text-muted-foreground">
          <MessageSquareText className="size-5" aria-hidden="true" />
          No conversations yet. Ask your first question.
        </p>
      ) : (
        <>
          <ul className="flex flex-col gap-0.5">
            {state.items.map((c) => {
              const active = c.id === activeId;
              return (
                <li key={c.id} className="group relative">
                  <Link
                    href={`/chat/${c.id}`}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "block rounded-md px-2.5 py-1.5 pr-8 text-sm transition-colors hover:bg-muted",
                      active && "bg-muted font-medium",
                    )}
                  >
                    <span className="block truncate">{c.title || "New conversation"}</span>
                    <span className="block text-[11px] text-muted-foreground">{formatRelative(c.last_message_at)}</span>
                  </Link>
                  <div className="absolute top-1.5 right-1">
                    <DropdownMenu>
                      <DropdownMenuTrigger
                        render={
                          <Button
                            variant="ghost"
                            size="icon-xs"
                            className="opacity-60 group-hover:opacity-100 focus-visible:opacity-100"
                            aria-label={`Actions for ${c.title || "conversation"}`}
                          />
                        }
                      >
                        <Ellipsis />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-36">
                        <DropdownMenuItem onClick={() => setRenameTarget(c)}>Rename…</DropdownMenuItem>
                        <DropdownMenuItem variant="destructive" onClick={() => setDeleteTarget(c)}>
                          Delete…
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </li>
              );
            })}
          </ul>
          {state.moreError ? <ErrorAlert error={state.moreError} onRetry={() => void conversations.loadMore()} /> : null}
          {state.hasMore ? (
            <Button
              variant="ghost"
              size="sm"
              className="mt-1 w-full text-muted-foreground"
              disabled={state.loadingMore}
              onClick={() => void conversations.loadMore()}
            >
              {state.loadingMore ? "Loading…" : "Load more"}
            </Button>
          ) : null}
        </>
      )}

      <Dialog open={renameTarget !== null} onOpenChange={(open) => !open && setRenameTarget(null)}>
        <DialogContent>
          {renameTarget ? (
            <RenameForm
              key={renameTarget.id}
              conversation={renameTarget}
              onDone={() => {
                setRenameTarget(null);
                void conversations.refresh();
              }}
            />
          ) : null}
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title="Delete this conversation?"
        description={`“${deleteTarget?.title || "New conversation"}” and its answers are deleted for good. Documents aren't affected.`}
        confirmLabel="Delete conversation"
        onConfirm={async () => {
          if (!deleteTarget) return;
          await chatApi.deleteConversation(deleteTarget.id);
          if (deleteTarget.id === activeId) router.push("/chat");
          await conversations.refresh();
        }}
      />
    </nav>
  );
}

function RenameForm({ conversation, onDone }: { conversation: Conversation; onDone: () => void }) {
  const [title, setTitle] = useState(conversation.title);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const trimmed = title.trim();
  return (
    <form
      className="contents"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!trimmed) return;
        setPending(true);
        setError(null);
        try {
          await chatApi.renameConversation(conversation.id, trimmed);
          onDone();
        } catch (err) {
          setError(err);
        } finally {
          setPending(false);
        }
      }}
    >
      <DialogHeader>
        <DialogTitle>Rename conversation</DialogTitle>
        <DialogDescription>Only you see your conversations.</DialogDescription>
      </DialogHeader>
      <Input aria-label="Title" maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending || !trimmed || trimmed === conversation.title}>
          {pending ? "Saving…" : "Rename"}
        </Button>
      </DialogFooter>
    </form>
  );
}
