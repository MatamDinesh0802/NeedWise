"use client";

import { useId, type ReactNode } from "react";

import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";

/** Label + control + hint/error, wired for screen readers via the render-prop ids. */
export function Field({
  label,
  hint,
  error,
  children,
  className,
}: {
  label: string;
  hint?: ReactNode;
  error?: string | null;
  children: (ids: { id: string; describedBy: string | undefined; invalid: boolean }) => ReactNode;
  className?: string;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label htmlFor={id}>{label}</Label>
      {children({ id, describedBy, invalid: Boolean(error) })}
      {hint ? (
        <p id={hintId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export interface SelectOption<V extends string> {
  value: V;
  label: string;
  disabled?: boolean;
}

/** shadcn/Base UI Select with a plain options list. */
export function SimpleSelect<V extends string>({
  value,
  onChange,
  options,
  disabled,
  id,
  ariaLabel,
  describedBy,
  invalid,
  className,
  size,
}: {
  value: V;
  onChange: (value: V) => void;
  options: readonly SelectOption<V>[];
  disabled?: boolean;
  id?: string;
  ariaLabel?: string;
  /** From Field: its hint and error, so a screen reader announces them with the select. */
  describedBy?: string;
  invalid?: boolean;
  className?: string;
  size?: "sm" | "default";
}) {
  return (
    <Select
      value={value}
      items={options.map((o) => ({ value: o.value, label: o.label }))}
      onValueChange={(next) => {
        if (next != null) onChange(next as V);
      }}
      disabled={disabled}
    >
      <SelectTrigger
        id={id}
        aria-label={ariaLabel}
        aria-describedby={describedBy}
        aria-invalid={invalid || undefined}
        size={size}
        className={cn("min-w-32", className)}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value} disabled={option.disabled}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
