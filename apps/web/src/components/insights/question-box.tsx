"use client";

import { ArrowUp, Sparkles } from "lucide-react";
import { useId, useState, type KeyboardEvent, type RefObject } from "react";

import { Button } from "@/components/ui/button";
import { QUESTION_MAX_LENGTH } from "@/lib/analytics/api";
import { questionError } from "@/lib/analytics/ask";
import { cn } from "@/lib/utils";

/**
 * The question box. Enter asks, Shift+Enter adds a line. Example questions fill the box (they
 * don't send it), so people can adjust the period or metric first.
 */
export function QuestionBox({
  value,
  onChange,
  onAsk,
  busy,
  examples,
  inputRef,
}: {
  value: string;
  onChange: (value: string) => void;
  onAsk: (question: string) => void;
  busy: boolean;
  examples: readonly string[];
  inputRef: RefObject<HTMLTextAreaElement | null>;
}) {
  const id = useId();
  const [touched, setTouched] = useState(false);
  const problem = questionError(value);
  const showProblem = touched && problem !== null && value.length > 0;

  function submit() {
    setTouched(true);
    if (busy || problem) return;
    onAsk(value.trim());
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <form
        className="flex flex-col gap-1 rounded-2xl border bg-background p-2 shadow-sm focus-within:ring-2 focus-within:ring-ring/40"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label htmlFor={id} className="px-2 pt-1 text-xs font-medium text-muted-foreground">
          Ask a question about the data
        </label>
        <textarea
          id={id}
          ref={inputRef}
          rows={2}
          value={value}
          maxLength={QUESTION_MAX_LENGTH}
          disabled={busy}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="e.g. Monthly revenue trend this year and the top 3 drivers"
          aria-describedby={`${id}-hint${showProblem ? ` ${id}-error` : ""}`}
          aria-invalid={showProblem || undefined}
          className="max-h-48 min-h-12 resize-none bg-transparent px-2 py-1 text-sm outline-none [field-sizing:content] placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-70"
        />
        <div className="flex items-center justify-between gap-2 px-2">
          <span id={`${id}-hint`} className="text-[11px] text-muted-foreground">
            Enter asks · Shift+Enter for a new line
            {value.length > QUESTION_MAX_LENGTH - 200 ? ` · ${QUESTION_MAX_LENGTH - value.length} characters left` : ""}
          </span>
          <Button type="submit" size="sm" disabled={busy || value.trim().length === 0}>
            <ArrowUp data-icon="inline-start" />
            {busy ? "Working…" : "Ask"}
          </Button>
        </div>
        {showProblem ? (
          <p id={`${id}-error`} className="px-2 text-xs text-destructive">
            {problem}
          </p>
        ) : null}
      </form>
      {examples.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Example questions">
          <Sparkles className="size-3.5 text-muted-foreground" aria-hidden="true" />
          {examples.map((example) => (
            <button
              key={example}
              type="button"
              disabled={busy}
              onClick={() => {
                onChange(example);
                inputRef.current?.focus();
              }}
              className={cn(
                "rounded-full border bg-background px-2.5 py-1 text-left text-xs text-muted-foreground transition-colors",
                "hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none disabled:opacity-60",
              )}
            >
              {example}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
