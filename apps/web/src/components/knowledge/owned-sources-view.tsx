"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";

import { SourcesSection } from "@/components/admin/sources-section";
import { SignedInGate } from "@/components/auth/signed-in-gate";
import { buttonVariants } from "@/components/ui/button";

/** Org admins manage every source (here too, as in Admin › Sources); collection owners theirs. */
export function OwnedSourcesView() {
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5">
      <Link href="/knowledge" className={buttonVariants({ variant: "ghost", size: "sm", className: "-ml-2 w-fit" })}>
        <ArrowLeft data-icon="inline-start" />
        Knowledge Hub
      </Link>
      <header>
        <h2 className="text-xl font-semibold tracking-tight">Sources</h2>
        <p className="text-sm text-muted-foreground">
          Websites, repositories, Slack channels and Drive folders that sync into the collections you own. Administrators see every source.
        </p>
      </header>
      <SignedInGate purpose="see the sources of the collections you own">
        {(me) => <SourcesSection embedded scope={me.org_role === "owner" || me.org_role === "admin" ? "all" : "owned"} />}
      </SignedInGate>
    </div>
  );
}
