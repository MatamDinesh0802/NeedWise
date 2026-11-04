"use client";

import { CircleAlert, Info, X } from "lucide-react";
import { useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";
import { toastStore } from "@/lib/stores/toasts";
import { cn } from "@/lib/utils";

export function Toaster() {
  const toasts = useSyncExternalStore(toastStore.subscribe, toastStore.getSnapshot, toastStore.getServerSnapshot);
  return (
    <div aria-live="polite" className="pointer-events-none fixed right-4 bottom-4 z-50 flex w-80 flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          role={t.tone === "error" ? "alert" : "status"}
          className={cn(
            "pointer-events-auto flex items-start gap-2 rounded-lg border bg-popover p-3 text-sm text-popover-foreground shadow-lg",
            t.tone === "error" && "border-destructive/40",
          )}
        >
          {t.tone === "error" ? (
            <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden="true" />
          ) : (
            <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          )}
          <div className="min-w-0 flex-1">
            <p className="font-medium">{t.title}</p>
            {t.description ? <p className="text-muted-foreground">{t.description}</p> : null}
          </div>
          <Button variant="ghost" size="icon-xs" aria-label="Dismiss" onClick={() => toastStore.dismiss(t.id)}>
            <X />
          </Button>
        </div>
      ))}
    </div>
  );
}
