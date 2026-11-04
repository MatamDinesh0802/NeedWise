"use client";

import { LockKeyhole } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState, ErrorAlert } from "@/components/data/states";
import { Skeleton } from "@/components/ui/skeleton";
import { SignInLink } from "@/components/user-menu";
import { useSession } from "@/hooks/use-session";
import type { Me } from "@/lib/session";

/** Renders `children(me)` once signed in; loading, signed-out and error states otherwise. */
export function SignedInGate({ children, purpose }: { children: (me: Me) => ReactNode; purpose: string }) {
  const { state, reload } = useSession();

  if (state.kind === "loading") {
    return (
      <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading your session">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (state.kind === "error") return <ErrorAlert error={new Error(state.message)} onRetry={() => void reload()} />;
  if (state.kind === "signed_out") {
    return (
      <EmptyState
        icon={LockKeyhole}
        title="Sign in to continue"
        description={`You need to be signed in to ${purpose}.`}
        action={<SignInLink />}
      />
    );
  }
  return <>{children(state.me)}</>;
}
