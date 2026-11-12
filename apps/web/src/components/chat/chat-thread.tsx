"use client";

import { MessageSquareText, SearchX, ShieldCheck } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { AssistantMessage } from "@/components/chat/assistant-message";
import { useChatShell } from "@/components/chat/chat-context";
import { Composer } from "@/components/chat/composer";
import { RunApprovals } from "@/components/chat/research-activity";
import { SourcesPanel, type PanelSources } from "@/components/chat/sources-panel";
import { EmptyState, ErrorAlert } from "@/components/data/states";
import { BudgetExceededAlert } from "@/components/usage/budget-exceeded-alert";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { useSession } from "@/hooks/use-session";
import { useStore } from "@/hooks/use-store";
import { useIsMobile } from "@/hooks/use-mobile";
import { answerFromMessage, answerFromRun, approvalsByRunId, type AnswerView } from "@/lib/chat/answer";
import { MODE_HINTS } from "@/lib/chat/research";
import { createThreadController } from "@/lib/chat/thread-controller";
import { questionForRun, threadItems } from "@/lib/chat/thread";
import { errorMessage } from "@/lib/error-messages";
import { toast } from "@/lib/stores/toasts";
import { budgetBlockFromError, type BudgetBlock } from "@/lib/usage/budget-error";
import { cn } from "@/lib/utils";
import { workApi, workKeys, type Approval } from "@/lib/work/api";
import { getNotificationsStore } from "@/lib/work/notifications";

export const STARTERS = [
  "How many weeks of parental leave do we get?",
  "What's the hotel limit in London?",
  "How do I restart the fleet controller?",
];

/** /chat/[id]: reads the id (runtime data, so this sits behind Suspense). */
export function ChatThreadRoute() {
  const { id } = useParams<{ id: string }>();
  return <ChatThread key={id} conversationId={id} />;
}

export function ChatThread({ conversationId }: { conversationId: string | null }) {
  const router = useRouter();
  const shell = useChatShell();
  const [controller] = useState(() =>
    createThreadController({
      conversationId,
      onConversationCreated: (id) => router.replace(`/chat/${id}`),
      onActivity: () => shell.refreshConversations(),
      onBusy: () =>
        toast({ title: "Still answering", description: "Wait for the current answer, or press Stop first." }),
      onRunEnded: (run) => {
        if (run.approvals.length === 0) return;
        void invalidateQueries(workKeys.approvals); // the stored approvals now include this run's
        void getNotificationsStore().refresh();
      },
    }),
  );
  useEffect(() => () => controller.dispose(), [controller]);
  const state = useStore(controller);
  const { mode, setMode } = shell;
  const { state: session } = useSession();
  const meId = session.kind === "signed_in" ? session.me.id : null;

  // Approvals proposed in this conversation, from the API (after a reload the run's events are
  // gone): the cards come back under the answer that proposed them, pending or decided.
  const approvals = useApiQuery(conversationId ? workKeys.conversationApprovals(conversationId) : null, () =>
    workApi.listApprovals("all", { conversationId: conversationId ?? undefined }),
  );
  const approvalsByRun = useMemo(
    () =>
      approvals.state.status === "success" && conversationId
        ? approvalsByRunId(approvals.state.data, conversationId)
        : new Map<string, Approval[]>(),
    [approvals.state, conversationId],
  );

  const [panel, setPanel] = useState<{ answerKey: string; runId: string | null; marker: string | null } | null>(null);
  const [blocked, setBlocked] = useState<BudgetBlock | null>(null);
  const isMobile = useIsMobile();
  const bottom = useRef<HTMLDivElement>(null);

  const messages = useMemo(() => state.conversation?.messages ?? [], [state.conversation]);
  const items = threadItems(messages, state.live, state.pendingUser);
  const running = state.live !== null && ["connecting", "searching", "streaming"].includes(state.live.phase);

  // Answers for the panel and for rendering, keyed like the thread items.
  const answers = new Map<string, AnswerView>();
  items.forEach((item, index) => {
    if (item.kind === "live") answers.set(item.key, answerFromRun(item.run));
    else if (item.message.role === "assistant") {
      const previous = items[index - 1];
      const question = previous?.kind === "message" && previous.message.role === "user" ? previous.message.content : null;
      const runId = item.message.run_id;
      const trace = runId ? (state.traces[runId] ?? null) : null;
      answers.set(item.key, answerFromMessage(item.message, question, trace, runId ? (state.research[runId] ?? null) : null));
    }
  });

  // Keep the newest content in view: after loading, on new messages and while answering.
  const liveText = state.live?.text.length ?? 0;
  const loadedId = state.conversation?.id ?? null;
  useEffect(() => {
    const frame = requestAnimationFrame(() => bottom.current?.scrollIntoView({ block: "end" }));
    return () => cancelAnimationFrame(frame);
  }, [items.length, liveText, loadedId]);

  async function send(text: string): Promise<boolean> {
    try {
      await controller.send(text, mode);
      setBlocked(null);
      return true;
    } catch (error) {
      // A used-up budget stays on screen above the composer (which keeps the question), naming
      // the budget and when it resets; a toast would vanish before it can be read.
      const block = budgetBlockFromError(error);
      if (block) setBlocked(block);
      else toast({ title: errorMessage(error).title, description: errorMessage(error).description, tone: "error" });
      return false;
    }
  }

  // The panel follows a live answer into its stored message when the run ends (same run id).
  const shown = panel
    ? (answers.get(panel.answerKey) ?? [...answers.values()].find((a) => panel.runId !== null && a.runId === panel.runId))
    : undefined;
  const panelData: PanelSources | null = shown
    ? { answerKey: shown.key, sources: shown.sources, text: shown.text, citations: shown.citations }
    : null;

  const thread =
    state.status === "not_found" ? (
      <EmptyState icon={SearchX} title="Conversation not found" description="It may have been deleted." />
    ) : state.status === "error" && !state.conversation ? (
      <ErrorAlert error={state.error} />
    ) : conversationId && !state.conversation ? (
      <div className="flex flex-col gap-4" aria-busy="true" aria-label="Loading conversation">
        <Skeleton className="ml-auto h-10 w-2/3 rounded-2xl" />
        <Skeleton className="h-24 w-full" />
      </div>
    ) : items.length === 0 ? (
      <Starters disabled={running || state.sending} onPick={(q) => void send(q)} />
    ) : (
      <ol className="flex flex-col gap-6">
        {items.map((item) => {
          if (item.kind === "message" && item.message.role === "user") {
            return (
              <li key={item.key} className="flex justify-end">
                <p className="max-w-[85%] rounded-2xl bg-primary px-4 py-2 text-sm whitespace-pre-wrap text-primary-foreground">
                  {item.message.content}
                </p>
              </li>
            );
          }
          const answer = answers.get(item.key);
          if (!answer) return null;
          const question = questionForRun(messages, answer.runId) ?? state.pendingUser?.content ?? null;
          return (
            <li key={item.key}>
              <AssistantMessage
                answer={answer}
                sourcesOpen={shown?.key === answer.key}
                onOpenSources={(marker) => setPanel({ answerKey: answer.key, runId: answer.runId, marker })}
                onRetry={question && !running ? () => void send(question) : undefined}
                onUpdated={(message) => controller.updateMessage(message)}
              >
                <RunApprovals
                  refs={answer.approvals}
                  stored={answer.runId ? (approvalsByRun.get(answer.runId) ?? []) : []}
                  meId={meId}
                />
              </AssistantMessage>
            </li>
          );
        })}
      </ol>
    );

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto px-4 py-6 md:px-8">
          <div className="mx-auto w-full max-w-3xl">
            {thread}
            <div ref={bottom} />
          </div>
        </div>
        <div className="border-t bg-background/80 px-4 py-3 backdrop-blur md:px-8">
          <div className="mx-auto w-full max-w-3xl space-y-1.5">
            {blocked ? <BudgetExceededAlert block={blocked} onDismiss={() => setBlocked(null)} className="mb-2" /> : null}
            <Composer
              running={running}
              sending={state.sending}
              onSend={send}
              onStop={() => void controller.cancel()}
              autoFocus={!conversationId}
              mode={mode}
              onModeChange={setMode}
            />
            {mode === "research" ? (
              <p className="flex items-center justify-center gap-1 text-center text-[11px] text-amber-800 dark:text-amber-300">
                <ShieldCheck className="size-3" aria-hidden="true" />
                {MODE_HINTS.research}
              </p>
            ) : (
              <p className="text-center text-[11px] text-muted-foreground">
                Answers come only from documents you can access, with sources. Check important details in the source.
              </p>
            )}
          </div>
        </div>
      </div>

      {panelData && !isMobile ? (
        <div className="hidden w-80 shrink-0 border-l md:flex md:flex-col xl:w-96">
          <SourcesPanel data={panelData} focused={panel?.marker ?? null} onClose={() => setPanel(null)} />
        </div>
      ) : null}
      {isMobile ? (
        <Sheet open={panelData !== null} onOpenChange={(open) => !open && setPanel(null)}>
          <SheetContent side="right" className="w-full p-0 sm:max-w-md">
            <SheetTitle className="sr-only">Sources</SheetTitle>
            {panelData ? <SourcesPanel data={panelData} focused={panel?.marker ?? null} onClose={() => setPanel(null)} /> : null}
          </SheetContent>
        </Sheet>
      ) : null}
    </div>
  );
}

function Starters({ onPick, disabled }: { onPick: (question: string) => void; disabled: boolean }) {
  return (
    <div className="flex flex-col items-center gap-6 py-12 text-center">
      <div className="flex size-12 items-center justify-center rounded-2xl border bg-muted/40">
        <MessageSquareText className="size-6 text-muted-foreground" aria-hidden="true" />
      </div>
      <div>
        <h2 className="text-lg font-semibold">What do you want to know?</h2>
        <p className="text-sm text-muted-foreground">Answers cite the documents they come from, and only use what you can access.</p>
      </div>
      <ul className="grid w-full max-w-xl gap-2 sm:grid-cols-3">
        {STARTERS.map((question) => (
          <li key={question}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onPick(question)}
              className={cn(
                "h-full w-full rounded-xl border bg-card px-3 py-2.5 text-left text-sm transition-colors hover:bg-muted/50",
                "focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none disabled:opacity-50",
              )}
            >
              {question}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
