"use client";

import { BadgeCheck, CalendarClock, CalendarX, Lock, ShieldOff, UserRoundX, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useId, type ReactNode } from "react";

import { EmptyState, ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { TrustBadge } from "@/components/knowledge/badges";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApiQuery } from "@/hooks/use-api-query";
import { formatDate, formatRelative } from "@/lib/format";
import type { Trust } from "@/lib/knowledge/api";
import {
  daysUntil,
  documentHref,
  documentLabel,
  governanceApi,
  governanceKeys,
  joinOr,
  overviewCounts,
  ownerText,
  type GovernanceCollection,
  type GovernanceOverview,
  type GovernedDocument,
  type OverviewCounts,
} from "@/lib/knowledge/governance";

const LISTS: readonly { key: keyof OverviewCounts; title: string; icon: LucideIcon }[] = [
  { key: "overdue", title: "Overdue reviews", icon: CalendarX },
  { key: "due_soon", title: "Due within 30 days", icon: CalendarClock },
  { key: "without_owner", title: "Without an owner", icon: UserRoundX },
  { key: "pending_official", title: "Waiting for official approval", icon: BadgeCheck },
  { key: "recent_demotions", title: "No longer official (30 days)", icon: ShieldOff },
];

/** Loads GET /governance/overview. Admins see every collection; others the ones they own. */
export function GovernanceOverviewSection({ footer }: { footer?: ReactNode }) {
  const { state, refetch } = useApiQuery(governanceKeys.overview, governanceApi.overview);
  if (state.status === "loading") return <TableSkeleton rows={6} />;
  if (state.status === "error") return <ErrorAlert error={state.error} onRetry={() => void refetch()} />;
  return (
    <>
      <RefreshingHint active={state.refreshing} />
      <GovernanceOverviewView overview={state.data} footer={footer} />
    </>
  );
}

export function GovernanceOverviewView({ overview, now, footer }: { overview: GovernanceOverview; now?: number; footer?: ReactNode }) {
  const counts = overviewCounts(overview);
  const collections = new Map(overview.collections.map((c) => [c.id, c]));
  const baseId = useId();

  if (overview.scope === "owned" && overview.collections.length === 0) {
    return (
      <EmptyState
        icon={BadgeCheck}
        title="You don't own any collections"
        description="Collection owners see their collections' reviews and official-status requests here. An administrator sets owners in Admin > Collections."
      />
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <p className="text-sm text-muted-foreground">
        {overview.scope === "all"
          ? `All ${overview.collections.length} collections in the organization.`
          : `The ${overview.collections.length === 1 ? "collection" : `${overview.collections.length} collections`} you own: ${overview.collections.map((c) => c.name).join(", ")}.`}{" "}
        Titles of documents you can&apos;t open (their collection, or their own access list) are withheld.
      </p>

      <nav aria-label="Lists on this page">
        <ul className="grid gap-2 sm:grid-cols-3 lg:grid-cols-5">
          {LISTS.map((list) => (
            <li key={list.key}>
              <a
                href={`#${baseId}-${list.key}`}
                className="flex h-full flex-col gap-1 rounded-xl border bg-card p-3 text-sm outline-none hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring/50"
              >
                <span className="flex items-center gap-1.5 text-muted-foreground">
                  <list.icon className="size-4" aria-hidden="true" />
                  {list.title}
                </span>
                <span className="text-2xl font-semibold tabular-nums">{counts[list.key]}</span>
              </a>
            </li>
          ))}
        </ul>
      </nav>

      <List id={`${baseId}-overdue`} title="Overdue reviews" empty="No review is overdue.">
        {overview.overdue.length ? (
          <ReviewTable documents={overview.overdue} collections={collections} now={now} due="overdue" />
        ) : null}
      </List>

      <List id={`${baseId}-due_soon`} title="Due within 30 days" empty="No review is due in the next 30 days.">
        {overview.due_soon.length ? (
          <ReviewTable documents={overview.due_soon} collections={collections} now={now} due="soon" />
        ) : null}
      </List>

      <List
        id={`${baseId}-without_owner`}
        title="Without an owner"
        description="Nobody is reminded to review these. Open a document to give it an owner."
        empty="Every document has an owner."
      >
        {overview.without_owner.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Document</TableHead>
                <TableHead>Collection</TableHead>
                <TableHead>Owner</TableHead>
                <TableHead>Last reviewed</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {overview.without_owner.map((doc) => (
                <TableRow key={doc.id}>
                  <TableCell className="max-w-80 whitespace-normal">
                    <DocumentCell doc={doc} collection={collections.get(doc.collection_id)} />
                  </TableCell>
                  <TableCell>{collections.get(doc.collection_id)?.name ?? "—"}</TableCell>
                  <TableCell className="text-muted-foreground">{ownerText(doc)}</TableCell>
                  <TableCell className="whitespace-nowrap">{doc.reviewed_at ? formatDate(doc.reviewed_at) : "Never"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </List>

      <List
        id={`${baseId}-pending_official`}
        title="Waiting for official approval"
        description="The approver decides in Approvals. Nobody approves their own request."
        empty="No request is waiting."
      >
        {overview.pending_official.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Document</TableHead>
                <TableHead>Requested by</TableHead>
                <TableHead>Waiting for</TableHead>
                <TableHead>Why</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {overview.pending_official.map((request) => (
                <TableRow key={request.document.id} className="align-top">
                  <TableCell className="max-w-72">
                    <DocumentCell doc={request.document} collection={collections.get(request.document.collection_id)} />
                    <p className="text-xs text-muted-foreground">{collections.get(request.document.collection_id)?.name}</p>
                  </TableCell>
                  <TableCell className="whitespace-nowrap">
                    {request.requested_by?.display_name ?? "Someone"}
                    <p className="text-xs text-muted-foreground" title={formatDate(request.requested_at)}>
                      {formatRelative(request.requested_at)}
                    </p>
                  </TableCell>
                  <TableCell>{joinOr(request.approvers.map((a) => a.display_name)) || "Nobody"}</TableCell>
                  <TableCell className="max-w-72 text-sm whitespace-pre-wrap text-muted-foreground">{request.note || "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </List>

      <List
        id={`${baseId}-recent_demotions`}
        title="No longer official (30 days)"
        description="An editor removed official status. That is immediate and recorded in the audit log."
        empty="Nothing was demoted in the last 30 days."
      >
        {overview.recent_demotions.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Document</TableHead>
                <TableHead>By</TableHead>
                <TableHead>When</TableHead>
                <TableHead>Now</TableHead>
                <TableHead>Why</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {overview.recent_demotions.map((d) => (
                <TableRow key={`${d.document_id}-${d.at}`} className="align-top">
                  <TableCell className="max-w-72">
                    <DocumentCell doc={{ id: d.document_id, title: d.title }} collection={collections.get(d.collection_id)} />
                  </TableCell>
                  <TableCell>{d.by?.display_name ?? "Someone"}</TableCell>
                  <TableCell className="whitespace-nowrap">{formatDate(d.at)}</TableCell>
                  <TableCell>{isTrust(d.to) ? <TrustBadge trust={d.to} /> : d.to}</TableCell>
                  <TableCell className="max-w-72 text-sm whitespace-pre-wrap text-muted-foreground">{d.note || "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </List>

      {footer}
    </div>
  );
}

function isTrust(value: string): value is Trust {
  return value === "official" || value === "reference" || value === "discussion";
}

function List({ id, title, description, empty, children }: { id: string; title: string; description?: string; empty: string; children: ReactNode }) {
  const headingId = `${id}-heading`;
  return (
    <section id={id} aria-labelledby={headingId} className="flex scroll-mt-20 flex-col gap-2">
      <div>
        <h4 id={headingId} className="font-medium">
          {title}
        </h4>
        {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {children ?? <p className="rounded-lg border border-dashed px-3 py-2 text-sm text-muted-foreground">{empty}</p>}
    </section>
  );
}

/** A link to the document, or its withheld label for a collection the caller can't open. */
function DocumentCell({ doc, collection }: { doc: Pick<GovernedDocument, "id" | "title">; collection: GovernanceCollection | undefined }) {
  if (!doc.title) {
    return (
      <span className="flex items-center gap-1.5 text-muted-foreground italic">
        <Lock className="size-3.5 shrink-0" aria-hidden="true" />
        {documentLabel(doc, collection)}
      </span>
    );
  }
  return (
    <Link href={documentHref(doc.id)} className="font-medium break-words text-primary hover:underline">
      {doc.title}
    </Link>
  );
}

function ReviewTable({
  documents,
  collections,
  now,
  due,
}: {
  documents: GovernedDocument[];
  collections: Map<string, GovernanceCollection>;
  now?: number;
  due: "overdue" | "soon";
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Document</TableHead>
          <TableHead>Collection</TableHead>
          <TableHead>Owner</TableHead>
          <TableHead>Last reviewed</TableHead>
          <TableHead>{due === "overdue" ? "Was due" : "Due"}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {documents.map((doc) => {
          const left = doc.next_review_at ? daysUntil(doc.next_review_at, now) : null;
          return (
            <TableRow key={doc.id}>
              <TableCell className="max-w-80 whitespace-normal">
                <span className="flex flex-wrap items-center gap-2">
                  <DocumentCell doc={doc} collection={collections.get(doc.collection_id)} />
                  {doc.trust === "official" ? <TrustBadge trust="official" /> : null}
                </span>
              </TableCell>
              <TableCell>{collections.get(doc.collection_id)?.name ?? "—"}</TableCell>
              <TableCell className={doc.owner && !doc.owner_inactive ? undefined : "text-muted-foreground"}>{ownerText(doc)}</TableCell>
              <TableCell className="whitespace-nowrap">
                {doc.reviewed_at ? `${formatDate(doc.reviewed_at)}${doc.reviewed_by ? ` by ${doc.reviewed_by.display_name}` : ""}` : "Never"}
              </TableCell>
              <TableCell className="whitespace-nowrap">
                {formatDate(doc.next_review_at)}
                {left !== null ? (
                  <p className={due === "overdue" ? "text-xs text-amber-700 dark:text-amber-400" : "text-xs text-muted-foreground"}>
                    {left < 0 ? `${-left} ${-left === 1 ? "day" : "days"} ago` : left === 0 ? "today" : `in ${left} ${left === 1 ? "day" : "days"}`}
                  </p>
                ) : null}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
