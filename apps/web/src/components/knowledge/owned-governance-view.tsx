"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";

import { SignedInGate } from "@/components/auth/signed-in-gate";
import { GovernanceOverviewSection } from "@/components/knowledge/governance-overview";
import { buttonVariants } from "@/components/ui/button";

export function OwnedGovernanceView() {
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5">
      <Link href="/knowledge" className={buttonVariants({ variant: "ghost", size: "sm", className: "-ml-2 w-fit" })}>
        <ArrowLeft data-icon="inline-start" />
        Knowledge Hub
      </Link>
      <header>
        <h2 className="text-xl font-semibold tracking-tight">Governance</h2>
        <p className="text-sm text-muted-foreground">
          Reviews, owners and official-status requests in the collections you own. Administrators see every collection.
        </p>
      </header>
      <SignedInGate purpose="see the collections you own">{() => <GovernanceOverviewSection />}</SignedInGate>
    </div>
  );
}
