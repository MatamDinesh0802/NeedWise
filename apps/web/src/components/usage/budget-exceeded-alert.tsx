"use client";

import { Clock, OctagonX, X } from "lucide-react";
import Link from "next/link";

import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useSession } from "@/hooks/use-session";
import { cn } from "@/lib/utils";
import { budgetBlockCopy, type BudgetBlock, type Viewer } from "@/lib/usage/budget-error";

/**
 * What a person sees instead of a generic error when a budget stops their request: which budget,
 * how much is spent, when it resets and who can raise it.
 */
export function BudgetExceededAlert({ block, onDismiss, className }: { block: BudgetBlock; onDismiss?: () => void; className?: string }) {
  const { state } = useSession();
  const viewer = state.kind === "signed_in" ? { id: state.me.id, org_role: state.me.org_role } : null;
  return <BudgetExceededView block={block} viewer={viewer} onDismiss={onDismiss} className={className} />;
}

/** The alert without the session: for tests and callers that already know who is looking. */
export function BudgetExceededView({
  block,
  viewer,
  now,
  onDismiss,
  className,
}: {
  block: BudgetBlock;
  viewer: Viewer | null;
  now?: Date;
  onDismiss?: () => void;
  className?: string;
}) {
  const copy = budgetBlockCopy(block, viewer, now);
  return (
    <Alert variant="destructive" data-code="budget_exceeded" className={cn("text-foreground", className)}>
      <OctagonX aria-hidden="true" />
      <AlertTitle>{copy.title}</AlertTitle>
      <AlertDescription className="flex flex-col gap-1 text-foreground/90">
        <p>{copy.description}</p>
        {copy.resets ? (
          <p className="flex items-center gap-1.5">
            <Clock className="size-3.5 shrink-0" aria-hidden="true" />
            {copy.resets}
          </p>
        ) : null}
        <p>{copy.who}</p>
        <p className="flex flex-wrap gap-x-3 gap-y-1 pt-0.5">
          {copy.manageHref ? (
            <Link href={copy.manageHref} className="font-medium underline underline-offset-4">
              Open budgets
            </Link>
          ) : null}
          <Link href="/analytics" className="font-medium underline underline-offset-4">
            See usage
          </Link>
        </p>
      </AlertDescription>
      {onDismiss ? (
        <AlertAction>
          <Button variant="ghost" size="icon-sm" onClick={onDismiss} aria-label="Dismiss">
            <X />
          </Button>
        </AlertAction>
      ) : null}
    </Alert>
  );
}
