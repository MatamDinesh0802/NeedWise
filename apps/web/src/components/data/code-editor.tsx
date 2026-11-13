"use client";

import { CircleAlert } from "lucide-react";
import { useRef, type UIEvent } from "react";

import { lineRange } from "@/lib/admin/workflows";
import { cn } from "@/lib/utils";

export interface EditorProblem {
  /** 1-based; null for a problem with the whole text. */
  line: number | null;
  /** Where in the document, in words (e.g. "Step “send” › tool"); "" for none. */
  where: string;
  message: string;
}

/**
 * A plain-text code editor: a textarea (so typing, selection, undo, screen readers and the
 * keyboard work as everywhere: Tab leaves the field) with line numbers, a mark on every line
 * that has a problem, and the problems listed underneath. Each problem's "Line N" button puts
 * the cursor on that line.
 */
export function CodeEditor({
  id,
  value,
  onChange,
  problems = [],
  rows = 18,
  describedBy,
  disabled,
  checking = false,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  problems?: readonly EditorProblem[];
  rows?: number;
  /** From Field: its hint and error ids. */
  describedBy?: string;
  disabled?: boolean;
  /** The problems are being re-checked (shown as a hint, problems stay until the answer). */
  checking?: boolean;
}) {
  const textarea = useRef<HTMLTextAreaElement>(null);
  const gutter = useRef<HTMLDivElement>(null);
  const lines = Math.max(1, value.split("\n").length);
  const marked = new Map<number, string>();
  for (const problem of problems) {
    if (problem.line != null && !marked.has(problem.line)) marked.set(problem.line, problem.message);
  }
  const problemsId = `${id}-problems`;

  function syncScroll(event: UIEvent<HTMLTextAreaElement>) {
    if (gutter.current) gutter.current.scrollTop = event.currentTarget.scrollTop;
  }

  function jumpTo(line: number) {
    const area = textarea.current;
    if (!area) return;
    const { start, end } = lineRange(value, line);
    area.focus();
    area.setSelectionRange(start, end);
    // Keep the line in view: the textarea scrolls to the selection on focus in most browsers;
    // this covers the rest (20px per line, the leading below).
    area.scrollTop = Math.max(0, (line - 3) * 20);
  }

  return (
    <div className="flex flex-col gap-2">
      <div
        className={cn(
          "flex overflow-hidden rounded-lg border bg-background font-mono text-xs leading-5 focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50",
          problems.length > 0 && "border-destructive/60",
          disabled && "opacity-60",
        )}
      >
        <div
          ref={gutter}
          aria-hidden="true"
          className="shrink-0 overflow-hidden border-r bg-muted/40 py-2 text-right text-muted-foreground select-none"
          style={{ height: `${rows * 20 + 16}px` }}
        >
          {Array.from({ length: lines }, (_, index) => {
            const line = index + 1;
            const problem = marked.get(line);
            return (
              <div
                key={line}
                title={problem}
                className={cn("flex items-center justify-end gap-1 px-2 tabular-nums", problem && "bg-destructive/10 font-semibold text-destructive")}
              >
                {problem ? <span className="size-1.5 rounded-full bg-destructive" /> : null}
                {line}
              </div>
            );
          })}
        </div>
        <textarea
          ref={textarea}
          id={id}
          rows={rows}
          value={value}
          disabled={disabled}
          wrap="off"
          spellCheck={false}
          autoCapitalize="none"
          autoCorrect="off"
          onChange={(event) => onChange(event.target.value)}
          onScroll={syncScroll}
          aria-invalid={problems.length > 0 || undefined}
          aria-describedby={[describedBy, problems.length ? problemsId : null].filter(Boolean).join(" ") || undefined}
          className="min-w-0 flex-1 resize-y bg-transparent px-3 py-2 outline-none"
          style={{ minHeight: `${rows * 20 + 16}px` }}
        />
      </div>
      {problems.length > 0 ? (
        <div id={problemsId} role="group" aria-label="Problems in the definition" className="flex flex-col gap-1">
          <p className="flex items-center gap-1.5 text-xs font-medium text-destructive">
            <CircleAlert className="size-3.5" aria-hidden="true" />
            {problems.length === 1 ? "1 problem" : `${problems.length} problems`}
            {checking ? <span className="font-normal text-muted-foreground"> (checking again…)</span> : null}
          </p>
          <ul className="flex flex-col gap-1 text-xs">
            {problems.map((problem, index) => (
              <li key={index} className="flex flex-wrap items-baseline gap-x-2">
                {problem.line != null ? (
                  <button
                    type="button"
                    className="rounded font-mono font-medium text-destructive underline underline-offset-2 hover:no-underline focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
                    onClick={() => jumpTo(problem.line as number)}
                  >
                    Line {problem.line}
                  </button>
                ) : null}
                <span>
                  {problem.where ? <span className="font-medium">{problem.where}: </span> : null}
                  {problem.message}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
