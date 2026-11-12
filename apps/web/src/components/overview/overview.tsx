"use client";

import { SignedInGate } from "@/components/auth/signed-in-gate";
import { SystemStatusCard } from "@/components/system-status-card";
import { UsageTile } from "@/components/usage/usage-tile";
import { Commitments } from "@/components/work/commitments";
import { findNavItem } from "@/config/site";

/** The landing page: your model usage this month, what you said you'd do in meetings, and whether the system is healthy. */
export function Overview() {
  const item = findNavItem("/");
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h2 className="text-xl font-semibold tracking-tight">{item?.title ?? "Overview"}</h2>
        <p className="text-sm text-muted-foreground">{item?.description}</p>
      </header>
      <SignedInGate purpose="see your usage and commitments">
        {(me) => (
          <>
            <UsageTile me={me} />
            <Commitments />
          </>
        )}
      </SignedInGate>
      <SystemStatusCard />
    </div>
  );
}
