"use client";

import { CircleAlert, X } from "lucide-react";
import { useSyncExternalStore } from "react";

import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { getAuthErrorStore } from "@/lib/auth-error-store";

const getServerSnapshot = () => null;

/** Shows why a sign-in failed (`?auth_error=<code>`), once, and removes the flag from the URL. */
export function AuthErrorAlert() {
  const store = getAuthErrorStore();
  const message = useSyncExternalStore(store.subscribe, store.getSnapshot, getServerSnapshot);
  if (!message) return null;

  return (
    <Alert variant="destructive" className="mx-auto mb-6 w-full max-w-5xl">
      <CircleAlert aria-hidden="true" />
      <AlertTitle>{message.title}</AlertTitle>
      <AlertDescription>
        <p>{message.description}</p>
        {message.code ? (
          <p className="font-mono text-xs text-muted-foreground">Error code: {message.code}</p>
        ) : null}
      </AlertDescription>
      <AlertAction>
        <Button variant="ghost" size="icon-sm" onClick={store.dismiss} aria-label="Dismiss">
          <X />
        </Button>
      </AlertAction>
    </Alert>
  );
}
