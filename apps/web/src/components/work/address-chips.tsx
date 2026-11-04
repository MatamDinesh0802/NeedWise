"use client";

import { X } from "lucide-react";
import { useRef, type ClipboardEvent, type KeyboardEvent } from "react";

import { addAddresses, EMAIL_SHAPE } from "@/lib/work/approvals";
import { cn } from "@/lib/utils";

/**
 * Email addresses as chips. Enter, comma, semicolon or leaving the field turns the typed text
 * into chips; Backspace in an empty field removes the last one. Text still being typed is kept
 * in `input` (the payload includes it, so nothing typed is lost).
 */
export function AddressChips({
  id,
  values,
  input,
  onChange,
  describedBy,
  invalid,
  disabled,
  placeholder = "name@company.com",
}: {
  id: string;
  values: string[];
  input: string;
  onChange: (values: string[], input: string) => void;
  describedBy?: string;
  invalid?: boolean;
  disabled?: boolean;
  placeholder?: string;
}) {
  const ref = useRef<HTMLInputElement>(null);

  function commit() {
    if (input.trim()) onChange(addAddresses(values, input), "");
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if ((event.key === "Enter" || event.key === "," || event.key === ";") && input.trim()) {
      event.preventDefault();
      commit();
    } else if (event.key === "Enter") {
      event.preventDefault(); // never submit the whole form from an empty address field
    } else if (event.key === "Backspace" && !input && values.length > 0) {
      onChange(values.slice(0, -1), "");
    }
  }

  function onPaste(event: ClipboardEvent<HTMLInputElement>) {
    const text = event.clipboardData.getData("text");
    if (/[\s,;]/.test(text.trim())) {
      event.preventDefault();
      onChange(addAddresses(values, `${input} ${text}`), "");
    }
  }

  return (
    <div
      className={cn(
        "flex min-h-9 flex-wrap items-center gap-1.5 rounded-lg border bg-transparent px-2 py-1.5 text-sm shadow-xs focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/50",
        invalid && "border-destructive ring-destructive/20",
        disabled && "opacity-60",
      )}
      onClick={() => ref.current?.focus()}
    >
      {values.map((address, index) => {
        const valid = EMAIL_SHAPE.test(address);
        return (
          <span
            key={`${address}-${index}`}
            className={cn(
              "inline-flex max-w-full items-center gap-1 rounded-md bg-secondary px-2 py-0.5 text-xs text-secondary-foreground",
              !valid && "bg-destructive/10 text-destructive",
            )}
          >
            <span className="truncate">{address}</span>
            <button
              type="button"
              className="rounded-sm opacity-70 hover:opacity-100 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              aria-label={`Remove ${address}`}
              disabled={disabled}
              onClick={(e) => {
                e.stopPropagation();
                onChange(
                  values.filter((_, i) => i !== index),
                  input,
                );
              }}
            >
              <X className="size-3" aria-hidden="true" />
            </button>
          </span>
        );
      })}
      <input
        id={id}
        ref={ref}
        type="email"
        multiple
        autoComplete="off"
        spellCheck={false}
        value={input}
        disabled={disabled}
        placeholder={values.length === 0 ? placeholder : ""}
        aria-describedby={describedBy}
        aria-invalid={invalid || undefined}
        onChange={(e) => onChange(values, e.target.value)}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
        onBlur={commit}
        className="min-w-40 flex-1 bg-transparent py-0.5 outline-none placeholder:text-muted-foreground"
      />
    </div>
  );
}
