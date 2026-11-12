"use client";

import { CircleAlert, RefreshCw, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { errorMessage, type ErrorMessage } from "@/lib/error-messages";

/** An API error, explained (or `message`, already worded by the caller). Optionally with a retry button. */
export function ErrorAlert({
  error,
  message: worded,
  onRetry,
  className,
}: {
  error: unknown;
  message?: ErrorMessage;
  onRetry?: () => void;
  className?: string;
}) {
  const message = worded ?? errorMessage(error);
  return (
    <Alert variant="destructive" className={className}>
      <CircleAlert aria-hidden="true" />
      <AlertTitle>{message.title}</AlertTitle>
      <AlertDescription>
        <p>{message.description}</p>
        {message.code ? <p className="font-mono text-xs text-muted-foreground">Error code: {message.code}</p> : null}
      </AlertDescription>
      {onRetry ? (
        <AlertAction>
          <Button variant="outline" size="sm" onClick={onRetry}>
            <RefreshCw data-icon="inline-start" />
            Retry
          </Button>
        </AlertAction>
      ) : null}
    </Alert>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed px-6 py-10 text-center">
      <div className="flex size-10 items-center justify-center rounded-lg border bg-background">
        <Icon className="size-5 text-muted-foreground" aria-hidden="true" />
      </div>
      <p className="font-medium">{title}</p>
      {description ? <p className="max-w-md text-sm text-muted-foreground">{description}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

export function TableSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading">
      <Skeleton className="h-9 w-full" />
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-12 w-full" />
      ))}
    </div>
  );
}

/** Small "updating…" hint while cached data refreshes in the background. */
export function RefreshingHint({ active }: { active: boolean }) {
  if (!active) return null;
  return (
    <span className="flex items-center gap-1 text-xs text-muted-foreground" role="status">
      <RefreshCw className="size-3 animate-spin" aria-hidden="true" />
      Updating…
    </span>
  );
}
