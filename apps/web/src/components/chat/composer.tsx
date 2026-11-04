"use client";

import { ArrowUp, Square } from "lucide-react";
import { useId, useRef, useState, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export const MAX_QUESTION_LENGTH = 4000;

/** Enter sends, Shift+Enter adds a line. While a run is active, sending is off and Stop shows. */
export function Composer({
  running,
  sending,
  onSend,
  onStop,
  autoFocus,
}: {
  running: boolean;
  sending: boolean;
  onSend: (text: string) => Promise<boolean>;
  onStop: () => void;
  autoFocus?: boolean;
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
      className="flex items-end gap-2 rounded-2xl border bg-background p-2 shadow-sm focus-within:ring-2 focus-within:ring-ring/40"
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
        placeholder={running ? "Answering… press Stop to interrupt" : "Ask about your company's policies, docs and processes"}
        aria-describedby={hintId}
        className={cn(
          "max-h-48 min-h-9 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none [field-sizing:content] placeholder:text-muted-foreground disabled:cursor-not-allowed",
        )}
      />
      <span id={hintId} className="sr-only">
        Enter sends. Shift and Enter adds a new line.
      </span>
      {running ? (
        <Button type="button" variant="outline" size="icon" aria-label="Stop answering" title="Stop" onClick={onStop}>
          <Square className="fill-current" />
        </Button>
      ) : (
        <Button type="submit" size="icon" aria-label="Send" title="Send (Enter)" disabled={!canSend}>
          <ArrowUp />
        </Button>
      )}
    </form>
  );
}
