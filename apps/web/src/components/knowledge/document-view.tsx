"use client";

import { ArrowLeft, Download, FileQuestion, Layers, RotateCw, Search } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";

import { SignedInGate } from "@/components/auth/signed-in-gate";
import { EmptyState, ErrorAlert, TableSkeleton } from "@/components/data/states";
import { DocumentStatusLabel, FileTypeIcon, SensitivityBadge, TrustBadge } from "@/components/knowledge/badges";
import { LiveIndicator } from "@/components/knowledge/live-indicator";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { useLiveRefresh } from "@/hooks/use-live-refresh";
import { ApiError } from "@/lib/api";
import { formatBytes, formatDateTime, formatRelative } from "@/lib/format";
import { knowledgeApi, knowledgeKeys, type Chunk, type DocumentDetail } from "@/lib/knowledge/api";
import { fileTypeLabel, headingTrail } from "@/lib/knowledge/labels";

export function DocumentView() {
  const { id } = useParams<{ id: string }>();
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-5">
      <SignedInGate purpose="open this document">{() => <DocumentLoader key={id} documentId={id} />}</SignedInGate>
    </div>
  );
}

function DocumentLoader({ documentId }: { documentId: string }) {
  const key = knowledgeKeys.document(documentId);
  const { state, refetch } = useApiQuery(key, () => knowledgeApi.getDocument(documentId));
  const live = useLiveRefresh(key, state.status === "success" ? [state.data] : null);

  if (state.status === "loading") {
    return (
      <div className="flex flex-col gap-4" aria-busy="true">
        <Skeleton className="h-8 w-80" />
        <Skeleton className="h-32 w-full" />
        <TableSkeleton rows={3} />
      </div>
    );
  }
  if (state.status === "error") {
    if (state.error instanceof ApiError && state.error.status === 404) {
      return (
        <EmptyState icon={FileQuestion} title="Document not found" description="It doesn't exist, or it isn't shared with you." />
      );
    }
    return <ErrorAlert error={state.error} onRetry={() => void refetch()} />;
  }
  const doc = state.data;
  return (
    <>
      <Link
        href={`/knowledge/collections/${doc.collection_id}`}
        className={buttonVariants({ variant: "ghost", size: "sm", className: "-ml-2 w-fit" })}
      >
        <ArrowLeft data-icon="inline-start" />
        Collection
      </Link>
      <Header doc={doc} />
      <LiveIndicator live={live.live} stopped={live.stopped} onResume={live.resume} />
      <Versions doc={doc} />
      {/* Keyed by version + status, so chunks reload when a new version finishes indexing. */}
      <ChunkViewer key={`${doc.version}:${doc.status}`} doc={doc} />
    </>
  );
}

function Header({ doc }: { doc: DocumentDetail }) {
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);

  async function reindex() {
    setPending(true);
    setError(null);
    try {
      await knowledgeApi.reindex(doc.id);
      await Promise.all([
        invalidateQueries(knowledgeKeys.document(doc.id)),
        invalidateQueries(knowledgeKeys.documents(doc.collection_id)),
      ]);
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  const busy = doc.status === "pending" || doc.status === "processing";
  return (
    <header className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2">
          <FileTypeIcon mime={doc.mime_type} className="mt-1.5 size-5" />
          <div className="min-w-0">
            <h2 className="text-xl font-semibold tracking-tight break-words">{doc.title}</h2>
            <p className="text-sm text-muted-foreground break-all">{doc.filename}</p>
          </div>
        </div>
        <div className="flex gap-2">
          {doc.can_edit ? (
            <Button variant="outline" size="sm" onClick={() => void reindex()} disabled={pending || busy}>
              <RotateCw data-icon="inline-start" className={pending ? "animate-spin" : undefined} />
              Re-index
            </Button>
          ) : null}
          <a href={knowledgeApi.fileUrl(doc.id)} download={doc.filename} className={buttonVariants({ size: "sm" })}>
            <Download data-icon="inline-start" />
            Download
          </a>
        </div>
      </div>
      {error ? <ErrorAlert error={error} /> : null}
      <dl className="grid gap-x-6 gap-y-2 rounded-xl border p-4 text-sm sm:grid-cols-4">
        <Meta label="Status">
          <DocumentStatusLabel status={doc.status} />
        </Meta>
        <Meta label="Trust">
          <TrustBadge trust={doc.trust} />
        </Meta>
        <Meta label="Sensitivity">
          <SensitivityBadge sensitivity={doc.sensitivity} />
        </Meta>
        <Meta label="Type">{fileTypeLabel(doc.mime_type)}</Meta>
        <Meta label="Version">v{doc.version}</Meta>
        <Meta label="Size">{formatBytes(doc.size_bytes)}</Meta>
        <Meta label="Chunks">{doc.status === "ready" ? doc.chunk_count : "—"}</Meta>
        <Meta label="Updated">
          <span title={formatDateTime(doc.updated_at)}>{formatRelative(doc.updated_at)}</span>
        </Meta>
      </dl>
      {doc.status === "failed" && doc.error ? (
        <ErrorAlert error={new Error(doc.error)} />
      ) : null}
    </header>
  );
}

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function Versions({ doc }: { doc: DocumentDetail }) {
  const versions = [...doc.versions].sort((a, b) => b.version_no - a.version_no);
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>Version history</CardTitle>
        <CardDescription>Uploading a file with the same name adds a version; identical files are skipped.</CardDescription>
      </CardHeader>
      <CardContent>
        {versions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No versions yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Version</TableHead>
                <TableHead>Uploaded</TableHead>
                <TableHead>Indexed</TableHead>
                <TableHead className="text-right">Size</TableHead>
                <TableHead className="text-right">Chunks</TableHead>
                <TableHead>Embedding model</TableHead>
                <TableHead>SHA-256</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {versions.map((v) => (
                <TableRow key={v.id}>
                  <TableCell className="font-medium">
                    v{v.version_no}
                    {v.version_no === doc.version ? (
                      <Badge variant="secondary" className="ml-2">
                        Current
                      </Badge>
                    ) : null}
                  </TableCell>
                  <TableCell className="whitespace-nowrap">{formatDateTime(v.created_at)}</TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {v.processed_at ? formatDateTime(v.processed_at) : "Not yet"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{formatBytes(v.size_bytes)}</TableCell>
                  <TableCell className="text-right tabular-nums">{v.chunk_count}</TableCell>
                  <TableCell className="font-mono text-xs">{v.embedding_model ?? "—"}</TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground" title={v.content_hash}>
                    {v.content_hash.slice(0, 12)}…
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function ChunkViewer({ doc }: { doc: DocumentDetail }) {
  const { state, refetch } = useApiQuery(knowledgeKeys.chunks(doc.id), () => knowledgeApi.listChunks(doc.id));
  const [filter, setFilter] = useState("");
  const chunks = useMemo(() => (state.status === "success" ? state.data.items : []), [state]);
  const q = filter.trim().toLowerCase();
  const shown = useMemo(
    () =>
      q
        ? chunks.filter(
            (c) => c.text.toLowerCase().includes(q) || c.heading_path.some((h) => h.toLowerCase().includes(q)),
          )
        : chunks,
    [chunks, q],
  );
  const totalTokens = chunks.reduce((sum, c) => sum + c.token_count, 0);

  return (
    <section className="flex flex-col gap-3" aria-labelledby="chunks-heading">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id="chunks-heading" className="flex items-center gap-2 font-medium">
            <Layers className="size-4" aria-hidden="true" />
            What the assistant reads
          </h3>
          <p className="text-sm text-muted-foreground">
            The document is split into chunks. Search finds chunks, and answers cite them.
            {chunks.length ? ` ${chunks.length} chunks, ${totalTokens.toLocaleString()} tokens.` : ""}
          </p>
        </div>
        {chunks.length > 3 ? (
          <div className="relative w-full max-w-xs">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Find in chunks"
              aria-label="Find in chunks"
              className="pl-8"
            />
          </div>
        ) : null}
      </div>

      {state.status === "loading" ? (
        <TableSkeleton rows={3} />
      ) : state.status === "error" ? (
        <ErrorAlert error={state.error} onRetry={() => void refetch()} />
      ) : chunks.length === 0 ? (
        <EmptyState
          icon={Layers}
          title={doc.status === "ready" ? "No chunks" : "Not indexed yet"}
          description={
            doc.status === "ready"
              ? "This document had no text to index."
              : "Chunks appear here once processing finishes."
          }
        />
      ) : shown.length === 0 ? (
        <p className="text-sm text-muted-foreground">No chunk contains “{filter.trim()}”.</p>
      ) : (
        <ol className="flex flex-col gap-3">
          {shown.map((chunk) => (
            <ChunkCard key={chunk.id} chunk={chunk} />
          ))}
        </ol>
      )}
    </section>
  );
}

function ChunkCard({ chunk }: { chunk: Chunk }) {
  const trail = headingTrail(chunk.heading_path);
  return (
    <li id={`chunk-${chunk.ordinal}`} className="rounded-xl border bg-card">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b bg-muted/30 px-4 py-2 text-xs">
        <span className="font-mono font-medium text-muted-foreground">#{chunk.ordinal}</span>
        <span className="min-w-0 flex-1 font-medium text-foreground" title={trail ?? undefined}>
          {trail ?? <span className="text-muted-foreground italic">No heading</span>}
        </span>
        {chunk.page != null ? <span className="text-muted-foreground">Page {chunk.page}</span> : null}
        <span className="text-muted-foreground tabular-nums">{chunk.token_count} tokens</span>
      </div>
      <p className="max-w-prose px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap break-words">{chunk.text}</p>
    </li>
  );
}
