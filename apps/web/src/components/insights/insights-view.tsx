"use client";

import { ChartColumnBig, FileQuestion, LockKeyhole, Settings2 } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { SignedInGate } from "@/components/auth/signed-in-gate";
import { EmptyState, ErrorAlert } from "@/components/data/states";
import { buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { ApiError } from "@/lib/api";
import {
  analyticsApi,
  analyticsKeys,
  type AnalyticsAnswer,
  type AnalyticsModelAvailable,
  type AnalyticsModelUnavailable,
} from "@/lib/analytics/api";
import { unavailableCopy } from "@/lib/analytics/ask";
import { initialProgress, type ProgressState } from "@/lib/analytics/progress";
import { createProgressStream, type ProgressStream } from "@/lib/analytics/progress-stream";
import type { Me } from "@/lib/session";
import { toast } from "@/lib/stores/toasts";

import { AnswerView } from "./answer-view";
import { AskProgress } from "./ask-progress";
import { QuestionBox } from "./question-box";
import { HistoryList, ModelSummary } from "./side-panels";

/** /insights: analytical questions answered with a chart, the SQL behind it and checked figures. */
export function InsightsView() {
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h2 className="text-xl font-semibold tracking-tight">Insights</h2>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Ask about company data in plain words. Each answer shows the chart, the SQL that produced every number, and which
          figures in the text match the results. Queries run with your access: you only see rows you&apos;re allowed to.
        </p>
      </header>
      <SignedInGate purpose="ask questions about company data">{(me) => <Insights me={me} />}</SignedInGate>
    </div>
  );
}

function Insights({ me }: { me: Me }) {
  const model = useApiQuery(analyticsKeys.model, analyticsApi.model);
  if (model.state.status === "loading") {
    return (
      <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading the data model">
        <Skeleton className="h-24 w-full rounded-2xl" />
        <Skeleton className="h-6 w-2/3" />
      </div>
    );
  }
  if (model.state.status === "error") return <ErrorAlert error={model.state.error} onRetry={() => void model.refetch()} />;
  if (!model.state.data.available) return <Unavailable model={model.state.data} me={me} />;
  return <Workspace model={model.state.data} />;
}

export function Unavailable({ model, me }: { model: AnalyticsModelUnavailable; me: Me }) {
  const copy = unavailableCopy(model, me.org_role);
  return (
    <EmptyState
      icon={model.reason === "no_access" ? LockKeyhole : Settings2}
      title={copy.title}
      description={[copy.description, model.message].filter(Boolean).join(" ")}
      action={
        copy.action ? (
          <Link href={copy.action.href} className={buttonVariants({ variant: "outline", size: "sm" })}>
            {copy.action.label}
          </Link>
        ) : undefined
      }
    />
  );
}

interface Asking {
  question: string;
  startedAt: number;
  /** The answer's live steps, once the server has started it. */
  stream: ProgressStream | null;
}

const NO_PROGRESS = initialProgress();
const noStream = () => () => {};

function useProgress(stream: ProgressStream | null): ProgressState {
  return useSyncExternalStore(
    stream ? stream.subscribe : noStream,
    () => (stream ? stream.getSnapshot() : NO_PROGRESS),
    () => NO_PROGRESS,
  );
}

/** Example questions of every dataset, without repeats. */
export function allExamples(model: AnalyticsModelAvailable): string[] {
  const lists = model.datasets?.length ? model.datasets.map((d) => d.examples) : [model.examples];
  return [...new Set(lists.flat())].slice(0, 8);
}

function Workspace({ model }: { model: AnalyticsModelAvailable }) {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const runId = params.get("run");

  const [draft, setDraft] = useState("");
  const [asking, setAsking] = useState<Asking | null>(null);
  const [askError, setAskError] = useState<unknown>(null);
  const [latest, setLatest] = useState<AnalyticsAnswer | null>(null);
  const [focusAnswer, setFocusAnswer] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const askingRef = useRef<Asking | null>(null);

  // Leaving the page stops listening (the server still saves the answer to the history).
  useEffect(() => () => askingRef.current?.stream?.close(), []);
  const progress = useProgress(asking?.stream ?? null);

  const useLatest = runId !== null && latest?.id === runId;
  const saved = useApiQuery(runId && !useLatest ? analyticsKeys.run(runId) : null, () => analyticsApi.run(runId!));
  const answer = useLatest ? latest : saved.state.status === "success" ? saved.state.data : null;

  function done() {
    askingRef.current = null;
    setAsking(null);
    void invalidateQueries(analyticsKeys.runs);
  }

  /** The run ended: show the stored answer, or why it failed. */
  async function finished(current: Asking, runId: string, state: ProgressState) {
    if (askingRef.current !== current) return; // stopped waiting, or a newer question
    try {
      if (state.phase === "failed" && state.error?.code !== "stream_lost") {
        setAskError(new ApiError({ status: 500, title: "The answer failed", code: state.error?.code, detail: state.error?.message }));
        return;
      }
      // Completed (its answer is stored, failed answers too), or the stream was lost: the
      // stored answer says how it ended.
      const result = await analyticsApi.run(runId);
      setLatest(result);
      setFocusAnswer(true);
      setDraft("");
      router.push(`${pathname}?run=${encodeURIComponent(result.id)}`, { scroll: false });
    } catch (error) {
      setAskError(error);
    } finally {
      done();
    }
  }

  async function ask(question: string) {
    const current: Asking = { question, startedAt: Date.now(), stream: null };
    askingRef.current = current;
    setAsking(current);
    setAskError(null);
    try {
      const { run_id: runId } = await analyticsApi.startRun(question);
      if (askingRef.current !== current) return;
      const stream = createProgressStream(runId, { onDone: (state) => void finished(current, runId, state) });
      current.stream = stream;
      setAsking({ ...current });
    } catch (error) {
      setAskError(error);
      done();
    }
  }

  function stopWaiting() {
    asking?.stream?.close();
    toast({ title: "Stopped waiting", description: "If the answer finishes anyway, it appears in your answers." });
    // The server keeps going; look again once it has likely finished.
    window.setTimeout(() => void invalidateQueries(analyticsKeys.runs), 30_000);
    done();
  }

  function askAgain(question: string) {
    setDraft(question);
    inputRef.current?.focus();
    inputRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <div className="flex min-w-0 flex-col gap-5">
        <QuestionBox value={draft} onChange={setDraft} onAsk={(q) => void ask(q)} busy={asking !== null} examples={allExamples(model)} inputRef={inputRef} />

        {asking ? (
          <AskProgress question={asking.question} startedAt={asking.startedAt} progress={progress} onStop={stopWaiting} />
        ) : askError ? (
          <ErrorAlert error={askError} onRetry={draft.trim() ? () => void ask(draft.trim()) : undefined} />
        ) : null}

        {!asking && runId ? (
          answer ? (
            <AnswerView key={answer.id} answer={answer} focusOnShow={focusAnswer} onAskAgain={askAgain} />
          ) : saved.state.status === "error" ? (
            <SavedAnswerError error={saved.state.error} onRetry={() => void saved.refetch()} />
          ) : (
            <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading the answer">
              <Skeleton className="h-7 w-2/3" />
              <Skeleton className="h-64 w-full rounded-xl" />
            </div>
          )
        ) : null}

        {!asking && !runId && !askError ? (
          <EmptyState
            icon={ChartColumnBig}
            title={`Ask about ${model.name}`}
            description="You get a chart, a short explanation, and the SQL behind every number, which you can copy, refresh or download as CSV."
          />
        ) : null}
      </div>

      <aside className="flex min-w-0 flex-col gap-4" aria-label="About the data and your answers">
        <HistoryList activeId={runId} onOpen={() => setFocusAnswer(true)} />
        <ModelSummary model={model} />
      </aside>
    </div>
  );
}

function SavedAnswerError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  if (error instanceof ApiError && error.status === 404) {
    return (
      <EmptyState
        icon={FileQuestion}
        title="Answer not found"
        description="Saved answers are private: you can open only your own. It may also have been removed."
        action={
          <Link href="/insights" className={buttonVariants({ variant: "outline", size: "sm" })}>
            Ask a new question
          </Link>
        }
      />
    );
  }
  return <ErrorAlert error={error} onRetry={onRetry} />;
}
