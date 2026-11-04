"use client";

import { ArrowUp, MessageSquareText, Square, Wand2 } from "lucide-react";
import { useId, useRef, useState, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { MODE_LABELS, type ChatMode } from "@/lib/chat/research";
import { cn } from "@/lib/utils";

export const MAX_QUESTION_LENGTH = 4000;

const PLACEHOLDERS: Record<ChatMode, string> = {
  answer: "Ask about your company's policies, docs and processes",
  research: "Ask it to look into something or prepare an action, e.g. an email to your team",
};

/**
 * Enter sends, Shift+Enter adds a line. While a run is active, sending is off and Stop shows.
 * "Research & act" lets the assistant use tools and propose actions, which wait for approval.
 */
export function Composer({
  running,
  sending,
  onSend,
  onStop,
  autoFocus,
  mode,
  onModeChange,
}: {
  running: boolean;
  sending: boolean;
  onSend: (text: string) => Promise<boolean>;
  onStop: () => void;
  autoFocus?: boolean;
  mode: ChatMode;
  onModeChange: (mode: ChatMode) => void;
}) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  // Unique per instance: hidden, preserved routes keep their composer in the DOM.
  const id = useId();
  const hintId = `${id}-hint`;
  const busy = running || sending;
  const canSend = !busy && text.trim().length > 0;

  async function submit() {
    if (!canSend) return;
    const question = text;
    setText("");
    if (!(await onSend(question))) setText(question); // keep the question if sending failed
    ref.current?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    }
  }

  return (
    <form
      className={cn(
        "flex flex-col gap-1 rounded-2xl border bg-background p-2 shadow-sm focus-within:ring-2 focus-within:ring-ring/40",
        mode === "research" && "border-amber-500/50",
      )}
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <label htmlFor={id} className="sr-only">
        Ask a question
      </label>
      <textarea
        id={id}
        ref={ref}
        rows={1}
        value={text}
        maxLength={MAX_QUESTION_LENGTH}
        autoFocus={autoFocus}
        disabled={busy}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={running ? "Working… press Stop to interrupt" : PLACEHOLDERS[mode]}
        aria-describedby={hintId}
        className={cn(
          "max-h-48 min-h-9 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none [field-sizing:content] placeholder:text-muted-foreground disabled:cursor-not-allowed",
        )}
      />
      <span id={hintId} className="sr-only">
        Enter sends. Shift and Enter adds a new line.
      </span>
      <div className="flex items-center justify-between gap-2">
        <ModeSwitch mode={mode} onChange={onModeChange} disabled={busy} />
        {running ? (
          <Button type="button" variant="outline" size="icon" aria-label="Stop answering" title="Stop" onClick={onStop}>
            <Square className="fill-current" />
          </Button>
        ) : (
          <Button type="submit" size="icon" aria-label="Send" title="Send (Enter)" disabled={!canSend}>
            <ArrowUp />
          </Button>
        )}
      </div>
    </form>
  );
}

const MODE_ICONS = { answer: MessageSquareText, research: Wand2 } as const;

function ModeSwitch({ mode, onChange, disabled }: { mode: ChatMode; onChange: (mode: ChatMode) => void; disabled: boolean }) {
  return (
    <div role="group" aria-label="Assistant mode" className="inline-flex rounded-lg bg-muted p-0.5">
      {(["answer", "research"] as const).map((value) => {
        const Icon = MODE_ICONS[value];
        const active = mode === value;
        return (
          <button
            key={value}
            type="button"
            aria-pressed={active}
            disabled={disabled}
            onClick={() => onChange(value)}
            className={cn(
              "inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium text-muted-foreground transition-colors",
              "hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none disabled:opacity-60",
              active && "bg-background text-foreground shadow-sm",
            )}
          >
            <Icon className="size-3.5" aria-hidden="true" />
            {MODE_LABELS[value]}
          </button>
        );
      })}
    </div>
  );
}
