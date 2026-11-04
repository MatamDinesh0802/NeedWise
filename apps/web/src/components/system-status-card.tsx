"use client";

import { CircleAlert, CircleCheck, CircleX, LoaderCircle, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useSystemStatus } from "@/hooks/use-system-status";
import { KNOWN_COMPONENTS, toStatusView, type StatusTone } from "@/lib/system-status";
import { cn } from "@/lib/utils";

const TONE_STYLES: Record<StatusTone, { icon: typeof CircleCheck; className: string }> = {
  ok: { icon: CircleCheck, className: "text-emerald-600 dark:text-emerald-400" },
  warning: { icon: CircleAlert, className: "text-amber-600 dark:text-amber-400" },
  error: { icon: CircleX, className: "text-destructive" },
  neutral: { icon: LoaderCircle, className: "text-muted-foreground animate-spin" },
};

export function SystemStatusCard() {
  const { state, refreshing, refresh } = useSystemStatus();
  const view = toStatusView(state);
  const tone = TONE_STYLES[view.tone];
  const ToneIcon = tone.icon;
  const checkedAt = state.kind === "loading" ? null : state.checkedAt;

  return (
    <Card>
      <CardHeader>
        <CardTitle>System status</CardTitle>
        <CardDescription>Health of the core services behind this workspace.</CardDescription>
        <CardAction>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => void refresh()}
            disabled={refreshing}
            aria-label="Refresh system status"
          >
            <RefreshCw className={cn(refreshing && "animate-spin")} />
          </Button>
        </CardAction>
      </CardHeader>

      <CardContent className="flex flex-col gap-4">
        <div className="flex items-start gap-3" role="status" aria-live="polite">
          <ToneIcon className={cn("mt-0.5 size-5 shrink-0", tone.className)} aria-hidden="true" />
          <div className="flex flex-col gap-0.5">
            <p className="font-medium">{view.headline}</p>
            <p className="text-sm text-muted-foreground">{view.summary}</p>
          </div>
        </div>

        {state.kind === "loading" ? (
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {KNOWN_COMPONENTS.map((name) => (
              <li key={name}>
                <Skeleton className="h-11 w-full rounded-lg" />
              </li>
            ))}
          </ul>
        ) : view.components.length > 0 ? (
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {view.components.map((component) => (
              <li
                key={component.name}
                className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5"
                title={component.detail ?? undefined}
              >
                <span className="flex min-w-0 items-center gap-2">
                  <span
                    className={cn(
                      "size-2 shrink-0 rounded-full",
                      component.healthy ? "bg-emerald-500" : "bg-destructive",
                    )}
                    aria-hidden="true"
                  />
                  <span className="truncate text-sm font-medium">{component.label}</span>
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  <span className="font-mono text-xs tabular-nums text-muted-foreground">
                    {component.latency}
                  </span>
                  <Badge variant={component.healthy ? "secondary" : "destructive"}>
                    {component.healthy ? "ok" : "down"}
                  </Badge>
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </CardContent>

      <CardFooter className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {view.version ? <span>Version {view.version}</span> : null}
        {view.environment ? <span>Environment: {view.environment}</span> : null}
        <span className="ml-auto" suppressHydrationWarning>
          {checkedAt ? `Checked ${checkedAt.toLocaleTimeString()}` : "Refreshes every 10 seconds"}
        </span>
      </CardFooter>
    </Card>
  );
}
