"use client";

import { Check, CircleAlert, Copy, Library, RotateCw, Search, SearchX, ShieldAlert, Square, ThumbsDown, ThumbsUp } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import { AgentTraceLive, AgentTraceSummary } from "@/components/chat/agent-trace";
import { AnswerMarkdown, type ChipInfo } from "@/components/chat/answer-markdown";
import { ToolActivity } from "@/components/chat/research-activity";
import { BudgetExceededAlert } from "@/components/usage/budget-exceeded-alert";
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage } from "@/lib/error-messages";
import { chatApi, type Message, type Rating } from "@/lib/chat/api";
import { ABSTAIN_FALLBACK, type AnswerView } from "@/lib/chat/answer";
import { groupSources } from "@/lib/chat/thread";
import { formatUsage, withheldNotice } from "@/lib/chat/format";
import { budgetBlockFromRun } from "@/lib/usage/budget-error";
import { cn } from "@/lib/utils";

export function AssistantMessage({
  answer,
  onOpenSources,
  onRetry,
  onUpdated,
  sourcesOpen,
  children,
}: {
  answer: AnswerView;
  /** Open the sources panel for this answer, optionally focused on one marker. */
  onOpenSources: (marker: string | null) => void;
  onRetry?: () => void;
  onUpdated?: (message: Message) => void;
  sourcesOpen: boolean;
  /** Shown under the answer text (e.g. approval cards of a research run). */
  children?: ReactNode;
}) {
  const live = answer.phase === "searching" || answer.phase === "streaming";
  const chips = useMemo(() => {
    const map = new Map<string, ChipInfo>();
    const cited = new Set(answer.citations.map((c) => c.marker));
    for (const s of answer.sources) map.set(s.marker, { title: s.title, cited: cited.has(s.marker) });
    for (const c of answer.citations) if (!map.has(c.marker)) map.set(c.marker, { title: c.title, cited: true });
    return map;
  }, [answer.sources, answer.citations]);
  const groups = groupSources(answer.sources, answer.text, answer.citations);
  const notice = withheldNotice(answer.withheld);
  // A used-up budget is not a failure to retry: it says which budget and when it resets.
  const budgetBlock = answer.phase === "failed" ? budgetBlockFromRun(answer.error) : null;

  return (
    <article className="flex flex-col gap-2" aria-label="Answer">
      {answer.searchedFor ? (
        <p className="text-xs text-muted-foreground">
          <Search className="mr-1 inline size-3.5 align-[-2px]" aria-hidden="true" />
          Searched for: <span className="italic">{answer.searchedFor}</span>
        </p>
      ) : null}

      {notice ? (
        <Alert className="border-amber-500/40 text-amber-800 dark:text-amber-300">
          <ShieldAlert aria-hidden="true" />
          <AlertTitle>Some sources were held back</AlertTitle>
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      ) : null}

      {/* Agents: live list while answering (under "Searching…" until text arrives), then one line. */}
      {answer.trace && answer.phase === "streaming" ? <AgentTraceLive trace={answer.trace} /> : null}
      {answer.trace && !live ? <AgentTraceSummary trace={answer.trace} /> : null}
      {/* Research runs: what the assistant did (live under "Working…" until the text arrives). */}
      {answer.toolSteps.length > 0 && answer.phase !== "searching" ? <ToolActivity steps={answer.toolSteps} live={false} /> : null}

      <div aria-live={live ? "polite" : undefined} aria-busy={live || undefined}>
        {answer.phase === "searching" && !answer.text && answer.toolSteps.length > 0 ? (
          <ToolActivity steps={answer.toolSteps} live />
        ) : answer.phase === "searching" && !answer.text ? (
          <div className="flex flex-col gap-2">
            <Searching found={answer.sources.length} agents={answer.trace?.steps.length ?? 0} />
            {answer.trace ? <AgentTraceLive trace={answer.trace} /> : null}
          </div>
        ) : answer.abstained && answer.phase === "complete" ? (
          <div className="flex items-start gap-3 rounded-xl border bg-muted/30 px-4 py-3">
            <SearchX className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            <div className="text-sm text-muted-foreground">
              {/* Abstentions may still mention what was searched; their chips open the panel too. */}
              <AnswerMarkdown text={answer.text || ABSTAIN_FALLBACK} sources={chips} onCite={(marker) => onOpenSources(marker)} />
            </div>
          </div>
        ) : (
          <div className="relative">
            <AnswerMarkdown text={answer.text} sources={chips} onCite={(marker) => onOpenSources(marker)} />
            {answer.phase === "streaming" ? (
              <span aria-hidden="true" className="ml-0.5 inline-block h-4 w-1.5 animate-pulse rounded-sm bg-foreground/60 align-text-bottom" />
            ) : null}
          </div>
        )}
      </div>

      {children}

      {answer.phase === "cancelled" ? (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Square className="size-3" aria-hidden="true" />
          Stopped{answer.text ? "; the answer above is incomplete." : " before answering."}
        </p>
      ) : null}

      {budgetBlock ? (
        <BudgetExceededAlert block={budgetBlock} />
      ) : answer.phase === "failed" ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertTitle>The answer failed</AlertTitle>
          <AlertDescription>{answer.error?.message ?? "Something went wrong while answering."}</AlertDescription>
          {onRetry ? (
            <AlertAction>
              <Button variant="outline" size="sm" onClick={onRetry}>
                <RotateCw data-icon="inline-start" />
                Retry
              </Button>
            </AlertAction>
          ) : null}
        </Alert>
      ) : null}

      {!live ? (
        <Footer
          answer={answer}
          sourceCount={groups.cited.length + groups.other.length}
          onOpenSources={() => onOpenSources(null)}
          sourcesOpen={sourcesOpen}
          onUpdated={onUpdated}
        />
      ) : answer.sources.length > 0 ? (
        <Button variant="ghost" size="xs" className="w-fit text-muted-foreground" onClick={() => onOpenSources(null)}>
          <Library data-icon="inline-start" />
          {answer.sources.length} sources
        </Button>
      ) : null}
    </article>
  );
}

function Searching({ found, agents }: { found: number; agents: number }) {
  const label =
    found > 0
      ? `Found ${found} relevant passages, writing the answer…`
      : agents > 0
        ? `${agents} department agents are searching…`
        : "Searching your organization's knowledge…";
  return (
    <div className="flex flex-col gap-2" role="status">
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Search className="size-4 animate-pulse" aria-hidden="true" />
        {label}
      </p>
      <Skeleton className="h-3.5 w-11/12" />
      <Skeleton className="h-3.5 w-9/12" />
    </div>
  );
}

function Footer({
  answer,
  sourceCount,
  onOpenSources,
  sourcesOpen,
  onUpdated,
}: {
  answer: AnswerView;
  sourceCount: number;
  onOpenSources: () => void;
  sourcesOpen: boolean;
  onUpdated?: (message: Message) => void;
}) {
  const usage = formatUsage(answer.usage);
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-1">
        {sourceCount > 0 ? (
          <Button
            variant={sourcesOpen ? "secondary" : "ghost"}
            size="xs"
            className="text-muted-foreground"
            onClick={onOpenSources}
            aria-pressed={sourcesOpen}
          >
            <Library data-icon="inline-start" />
            {sourceCount} {sourceCount === 1 ? "source" : "sources"}
          </Button>
        ) : null}
        <CopyButton text={answer.text} />
        {answer.message && answer.phase !== "failed" ? <Feedback message={answer.message} onUpdated={onUpdated} /> : null}
        {usage ? <span className="ml-auto text-[11px] text-muted-foreground tabular-nums">{usage}</span> : null}
      </div>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  if (!text) return null;
  return (
    <Button
      variant="ghost"
      size="icon-xs"
      aria-label={copied ? "Copied" : "Copy answer"}
      title={copied ? "Copied" : "Copy answer"}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          setCopied(false);
        }
      }}
    >
      {copied ? <Check /> : <Copy />}
    </Button>
  );
}

function Feedback({ message, onUpdated }: { message: Message; onUpdated?: (message: Message) => void }) {
  const [commenting, setCommenting] = useState(false);
  const [comment, setComment] = useState(message.feedback_comment ?? "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);

  async function save(rating: Rating | null, text?: string) {
    setPending(true);
    setError(null);
    try {
      onUpdated?.(await chatApi.feedback(message.id, rating, text ?? null));
      setSaved(true);
      return true;
    } catch (err) {
      setError(err);
      return false;
    } finally {
      setPending(false);
    }
  }

  const rated = message.feedback;
  return (
    <>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label="Good answer"
        aria-pressed={rated === "up"}
        disabled={pending}
        className={cn(rated === "up" && "text-emerald-600 dark:text-emerald-400")}
        onClick={() => {
          setCommenting(false);
          void save(rated === "up" ? null : "up");
        }}
      >
        <ThumbsUp />
      </Button>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label="Bad answer"
        aria-pressed={rated === "down"}
        aria-expanded={commenting}
        disabled={pending}
        className={cn(rated === "down" && "text-destructive")}
        onClick={() => {
          if (rated === "down" && !commenting) {
            void save(null);
            return;
          }
          setSaved(false);
          setCommenting(true);
        }}
      >
        <ThumbsDown />
      </Button>
      {saved && !commenting ? <span className="text-[11px] text-muted-foreground" role="status">Thanks for the feedback</span> : null}
      {commenting ? (
        <form
          className="basis-full space-y-2 rounded-lg border p-2"
          onSubmit={async (e) => {
            e.preventDefault();
            if (await save("down", comment.trim())) setCommenting(false);
          }}
        >
          <label htmlFor={`feedback-${message.id}`} className="text-xs font-medium">
            What was wrong? (optional)
          </label>
          <Textarea
            id={`feedback-${message.id}`}
            rows={2}
            maxLength={2000}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="e.g. it cited the 2025 draft instead of the current policy"
          />
          {error ? <p className="text-xs text-destructive">{errorMessage(error).title}</p> : null}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" size="xs" onClick={() => setCommenting(false)} disabled={pending}>
              Cancel
            </Button>
            <Button type="submit" size="xs" disabled={pending}>
              {pending ? "Saving…" : "Send feedback"}
            </Button>
          </div>
        </form>
      ) : error ? (
        <span className="text-[11px] text-destructive">{errorMessage(error).title}</span>
      ) : null}
    </>
  );
}
