"use client";

import { ArrowLeft, Clock, Download, Ellipsis, Eye, FileText, PencilLine, RotateCw, SearchX, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useId, useState } from "react";

import { SignedInGate } from "@/components/auth/signed-in-gate";
import { ConfirmDialog } from "@/components/data/confirm-dialog";
import { EmptyState, ErrorAlert, TableSkeleton } from "@/components/data/states";
import {
  DocumentStatusLabel,
  FileTypeIcon,
  ScopeBadge,
  SensitivityBadge,
  TrustBadge,
} from "@/components/knowledge/badges";
import { LiveIndicator } from "@/components/knowledge/live-indicator";
import { UploadPanel } from "@/components/knowledge/upload-panel";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { useLiveRefresh } from "@/hooks/use-live-refresh";
import { ApiError } from "@/lib/api";
import { formatBytes, formatDate, formatRelative } from "@/lib/format";
import {
  knowledgeApi,
  knowledgeKeys,
  uploadDocument,
  type CollectionDetail,
  type KnowledgeDocument,
} from "@/lib/knowledge/api";
import { reviewBadge, reviewState } from "@/lib/knowledge/governance";
import { createUploadQueue } from "@/lib/knowledge/upload-queue";
import { cn } from "@/lib/utils";

const back = (
  <Link href="/knowledge" className={buttonVariants({ variant: "ghost", size: "sm", className: "-ml-2 w-fit" })}>
    <ArrowLeft data-icon="inline-start" />
    Knowledge Hub
  </Link>
);

export function CollectionView() {
  const { id } = useParams<{ id: string }>();
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5">
      {back}
      <SignedInGate purpose="open this collection">{() => <CollectionLoader key={id} collectionId={id} />}</SignedInGate>
    </div>
  );
}

function CollectionLoader({ collectionId }: { collectionId: string }) {
  const { state, refetch } = useApiQuery(knowledgeKeys.collection(collectionId), () =>
    knowledgeApi.getCollection(collectionId),
  );

  if (state.status === "loading") {
    return (
      <div className="flex flex-col gap-4" aria-busy="true">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-4 w-96" />
        <TableSkeleton />
      </div>
    );
  }
  if (state.status === "error") {
    if (state.error instanceof ApiError && state.error.status === 404) {
      return (
        <EmptyState
          icon={SearchX}
          title="Collection not found"
          description="It doesn't exist, or it hasn't been shared with you."
        />
      );
    }
    return <ErrorAlert error={state.error} onRetry={() => void refetch()} />;
  }
  return <Collection collection={state.data} />;
}

function Collection({ collection }: { collection: CollectionDetail }) {
  const [queue] = useState(() =>
    createUploadQueue<File>({
      upload: (file, options) => uploadDocument(collection.id, file, options),
      // Prefix of the list, this collection and its documents (counts and new rows).
      onUploaded: () => void invalidateQueries(knowledgeKeys.collections),
    }),
  );

  return (
    <>
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-xl font-semibold tracking-tight">{collection.name}</h2>
          <ScopeBadge scope={collection.scope} />
          {collection.can_edit ? (
            <Badge variant="outline">
              <PencilLine data-icon="inline-start" aria-hidden="true" />
              You can edit
            </Badge>
          ) : (
            <Badge variant="outline">
              <Eye data-icon="inline-start" aria-hidden="true" />
              View only
            </Badge>
          )}
        </div>
        {collection.description ? <p className="text-sm text-muted-foreground">{collection.description}</p> : null}
        <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          New documents default to <TrustBadge trust={collection.default_trust} /> and{" "}
          <SensitivityBadge sensitivity={collection.default_sensitivity} />
        </p>
      </header>

      {collection.can_edit ? <UploadPanel queue={queue} /> : null}

      <Documents collection={collection} />
    </>
  );
}

function Documents({ collection }: { collection: CollectionDetail }) {
  const key = knowledgeKeys.documents(collection.id);
  const { state, refetch } = useApiQuery(key, () => knowledgeApi.listDocuments(collection.id));
  const documents = state.status === "success" ? state.data.items : null;
  const live = useLiveRefresh(key, documents);
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeDocument | null>(null);
  const [rowError, setRowError] = useState<unknown>(null);
  const headingId = useId();

  async function reindex(doc: KnowledgeDocument) {
    setRowError(null);
    try {
      await knowledgeApi.reindex(doc.id);
      await invalidateQueries(key);
    } catch (error) {
      setRowError(error);
    }
  }

  return (
    <section className="flex flex-col gap-3" aria-labelledby={headingId}>
      <div className="flex flex-wrap items-center gap-3">
        <h3 id={headingId} className="font-medium">
          Documents {documents ? <span className="text-muted-foreground">({documents.length})</span> : null}
        </h3>
        <LiveIndicator live={live.live} stopped={live.stopped} onResume={live.resume} />
      </div>

      {rowError ? <ErrorAlert error={rowError} /> : null}

      {state.status === "loading" ? (
        <TableSkeleton />
      ) : state.status === "error" ? (
        <ErrorAlert error={state.error} onRetry={() => void refetch()} />
      ) : state.data.items.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No documents yet"
          description={collection.can_edit ? "Upload files above to add them to this collection." : "Editors haven't added any documents yet."}
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Title</TableHead>
              <TableHead>Trust</TableHead>
              <TableHead>Owner</TableHead>
              <TableHead>Review</TableHead>
              <TableHead>Sensitivity</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Chunks</TableHead>
              <TableHead className="text-right">Size</TableHead>
              <TableHead>Updated</TableHead>
              <TableHead className="w-10">
                <span className="sr-only">Actions</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {state.data.items.map((doc) => (
              <TableRow key={doc.id}>
                <TableCell className="max-w-80">
                  <div className="flex items-start gap-2">
                    <FileTypeIcon mime={doc.mime_type} className="mt-0.5" />
                    <div className="min-w-0">
                      <Link href={`/knowledge/documents/${doc.id}`} className="block truncate font-medium hover:underline" title={doc.title}>
                        {doc.title}
                      </Link>
                      <span className="block truncate text-xs text-muted-foreground" title={doc.filename}>
                        {doc.filename} · v{doc.version}
                      </span>
                    </div>
                  </div>
                </TableCell>
                <TableCell>
                  <div className="flex flex-col items-start gap-1">
                    <TrustBadge trust={doc.trust} />
                    {doc.official_request ? (
                      <Badge variant="outline" className="text-amber-800 dark:text-amber-300" title="Official status requested: waiting for approval">
                        <Clock data-icon="inline-start" aria-hidden="true" />
                        Official requested
                      </Badge>
                    ) : null}
                    {doc.archived_at ? (
                      <Badge variant="outline" title="Archived by a retention rule: kept, out of search and answers">
                        Archived
                      </Badge>
                    ) : null}
                  </div>
                </TableCell>
                <TableCell className="max-w-40 truncate" title={doc.owner?.display_name}>
                  {doc.owner ? doc.owner.display_name : <span className="text-muted-foreground">No owner</span>}
                </TableCell>
                <TableCell className="whitespace-nowrap">
                  <ReviewCell doc={doc} />
                </TableCell>
                <TableCell>
                  <SensitivityBadge sensitivity={doc.sensitivity} />
                </TableCell>
                <TableCell className="max-w-64">
                  <DocumentStatusLabel status={doc.status} />
                  {doc.status === "failed" && doc.error ? (
                    <p className="mt-0.5 line-clamp-2 text-xs text-destructive" title={doc.error}>
                      {doc.error}
                    </p>
                  ) : null}
                </TableCell>
                <TableCell className="text-right tabular-nums">{doc.status === "ready" ? doc.chunk_count : "—"}</TableCell>
                <TableCell className="text-right whitespace-nowrap tabular-nums">{formatBytes(doc.size_bytes)}</TableCell>
                <TableCell className="whitespace-nowrap text-muted-foreground">{formatRelative(doc.updated_at)}</TableCell>
                <TableCell>
                  {collection.can_edit ? (
                    <DropdownMenu>
                      <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label={`Actions for ${doc.title}`} />}>
                        <Ellipsis />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-44">
                        <DropdownMenuItem render={<a href={knowledgeApi.fileUrl(doc.id)} download={doc.filename} />}>
                          <Download />
                          Download
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => void reindex(doc)} disabled={doc.status === "pending" || doc.status === "processing"}>
                          <RotateCw />
                          Re-index
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem variant="destructive" onClick={() => setDeleteTarget(doc)}>
                          <Trash2 />
                          Delete…
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  ) : (
                    <a
                      href={knowledgeApi.fileUrl(doc.id)}
                      download={doc.filename}
                      className={buttonVariants({ variant: "ghost", size: "icon-sm" })}
                      aria-label={`Download ${doc.title}`}
                      title="Download original"
                    >
                      <Download />
                    </a>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title={`Delete “${deleteTarget?.title ?? ""}”?`}
        description="The document, all its versions and its search index entries are removed. The assistant can no longer use it. This can't be undone."
        confirmLabel="Delete document"
        onConfirm={async () => {
          if (!deleteTarget) return;
          await knowledgeApi.deleteDocument(deleteTarget.id);
          await invalidateQueries(knowledgeKeys.collections); // documents, counts and the list
        }}
      />
    </section>
  );
}

/** Overdue or due soon stands out; otherwise the next review date, or a dash without a cycle. */
export function ReviewCell({ doc, now }: { doc: KnowledgeDocument; now?: number }) {
  const label = reviewBadge(doc, now);
  if (label) {
    const overdue = reviewState(doc, now) === "overdue";
    return (
      <span className={cn("text-xs font-medium", overdue ? "text-amber-700 dark:text-amber-400" : "text-sky-700 dark:text-sky-400")}>
        {label}
      </span>
    );
  }
  if (!doc.next_review_at) return <span className="text-muted-foreground">—</span>;
  return <span className="text-xs text-muted-foreground">Next {formatDate(doc.next_review_at)}</span>;
}
