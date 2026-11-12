"use client";

import { BadgeCheck, FileText, LibraryBig, PencilLine } from "lucide-react";
import Link from "next/link";

import { SignedInGate } from "@/components/auth/signed-in-gate";
import { EmptyState, ErrorAlert, RefreshingHint } from "@/components/data/states";
import { ScopeBadge, SensitivityBadge, TrustBadge } from "@/components/knowledge/badges";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiQuery } from "@/hooks/use-api-query";
import { knowledgeApi, knowledgeKeys, type Collection } from "@/lib/knowledge/api";
import type { Me } from "@/lib/session";

export function CollectionsView() {
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <header>
        <h2 className="text-xl font-semibold tracking-tight">Knowledge Hub</h2>
        <p className="text-sm text-muted-foreground">
          Collections you can open. Answers only ever use documents from collections you have access to.
        </p>
      </header>
      <SignedInGate purpose="see your organization's knowledge">{(me) => <CollectionGrid me={me} />}</SignedInGate>
    </div>
  );
}

function CollectionGrid({ me }: { me: Me }) {
  const { state, refetch } = useApiQuery(knowledgeKeys.collections, knowledgeApi.listCollections);

  if (state.status === "loading") {
    return (
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-busy="true" aria-label="Loading collections">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-40 rounded-xl" />
        ))}
      </div>
    );
  }
  if (state.status === "error") return <ErrorAlert error={state.error} onRetry={() => void refetch()} />;
  if (state.data.items.length === 0) {
    return (
      <EmptyState
        icon={LibraryBig}
        title="No collections yet"
        description="You don't have access to any collections. Ask an administrator to share one with you."
      />
    );
  }

  // Collection owners (and admins) review their collections' governance.
  const governs = me.org_role === "owner" || me.org_role === "admin" || state.data.items.some((c) => c.owner?.id === me.id);
  return (
    <>
      <RefreshingHint active={state.refreshing} />
      {governs ? (
        <Link href="/knowledge/governance" className={buttonVariants({ variant: "outline", size: "sm", className: "w-fit" })}>
          <BadgeCheck data-icon="inline-start" aria-hidden="true" />
          Governance: reviews, owners and official requests
        </Link>
      ) : null}
      <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {state.data.items.map((collection) => (
          <li key={collection.id}>
            <CollectionCard collection={collection} />
          </li>
        ))}
      </ul>
    </>
  );
}

function CollectionCard({ collection }: { collection: Collection }) {
  return (
    <article className="relative flex h-full flex-col gap-3 rounded-xl border bg-card p-4 transition-colors hover:bg-muted/40 focus-within:ring-2 focus-within:ring-ring/50">
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-semibold leading-snug">
          {/* Stretched link: the whole card is clickable, badges keep their tooltips. */}
          <Link href={`/knowledge/collections/${collection.id}`} className="outline-none after:absolute after:inset-0">
            {collection.name}
          </Link>
        </h3>
        <div className="relative z-10 flex shrink-0 gap-1">
          {collection.can_edit ? (
            <Badge variant="outline" title="You can upload and manage documents here">
              <PencilLine data-icon="inline-start" aria-hidden="true" />
              Editor
            </Badge>
          ) : null}
          <ScopeBadge scope={collection.scope} />
        </div>
      </div>
      {collection.description ? (
        <p className="line-clamp-2 text-sm text-muted-foreground">{collection.description}</p>
      ) : null}
      {collection.owner ? <p className="text-xs text-muted-foreground">Owner: {collection.owner.display_name}</p> : null}
      <div className="relative z-10 mt-auto flex flex-wrap items-center gap-2 text-sm">
        <span className="mr-auto inline-flex items-center gap-1 text-muted-foreground">
          <FileText className="size-4" aria-hidden="true" />
          {collection.document_count} {collection.document_count === 1 ? "document" : "documents"}
        </span>
        <TrustBadge trust={collection.default_trust} />
        <SensitivityBadge sensitivity={collection.default_sensitivity} />
      </div>
    </article>
  );
}
