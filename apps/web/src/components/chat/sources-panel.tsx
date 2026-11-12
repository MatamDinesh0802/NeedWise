"use client";

import { ExternalLink, FileText, Lock, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef } from "react";

import { SensitivityBadge, TrustBadge } from "@/components/knowledge/badges";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiQuery } from "@/hooks/use-api-query";
import { ApiError } from "@/lib/api";
import { pdfPageUrl, type Source } from "@/lib/chat/api";
import { snippetParts } from "@/lib/chat/snippet";
import { groupSources } from "@/lib/chat/thread";
import { knowledgeApi, knowledgeKeys } from "@/lib/knowledge/api";
import { cn } from "@/lib/utils";

export interface PanelSources {
  /** Identifies the answer the panel belongs to. */
  answerKey: string;
  sources: Source[];
  text: string;
  citations: { marker: string }[];
}

/** Sources behind one answer: cited first, then "also searched". */
export function SourcesPanel({
  data,
  focused,
  onClose,
}: {
  data: PanelSources;
  focused: string | null;
  onClose: () => void;
}) {
  const { cited, other } = groupSources(data.sources, data.text, data.citations);
  return (
    <aside aria-label="Sources" className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <h2 className="text-sm font-semibold">Sources</h2>
        <Button variant="ghost" size="icon-sm" aria-label="Close sources" onClick={onClose}>
          <X />
        </Button>
      </div>
      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {cited.length === 0 && other.length === 0 ? (
          <p className="text-sm text-muted-foreground">This answer didn&apos;t use any sources.</p>
        ) : null}
        {cited.length > 0 ? (
          <section className="space-y-2">
            <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Cited</h3>
            {cited.map((s) => (
              <SourceCard key={`${data.answerKey}-${s.marker}`} source={s} cited focused={focused === s.marker} />
            ))}
          </section>
        ) : null}
        {other.length > 0 ? (
          <section className="space-y-2">
            <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Also searched</h3>
            {other.map((s) => (
              <SourceCard key={`${data.answerKey}-${s.marker}`} source={s} cited={false} focused={focused === s.marker} />
            ))}
          </section>
        ) : null}
      </div>
    </aside>
  );
}

function SourceCard({ source, cited, focused }: { source: Source; cited: boolean; focused: boolean }) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (focused) {
      ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      ref.current?.focus({ preventScroll: true });
    }
  }, [focused]);

  return (
    <article
      ref={ref}
      tabIndex={-1}
      aria-label={`Source ${source.marker.replace(/^S/, "")}: ${source.title}`}
      className={cn(
        "space-y-2 rounded-xl border bg-card p-3 text-sm outline-none transition-shadow",
        cited ? "border-primary/30" : "opacity-80",
        focused && "ring-2 ring-primary/50",
      )}
    >
      <div className="flex items-start gap-2">
        <span
          className={cn(
            "mt-0.5 inline-flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full border px-1 font-mono text-[11px]",
            cited ? "border-primary/30 bg-primary/10 text-primary" : "text-muted-foreground",
          )}
        >
          {source.marker.replace(/^S/, "")}
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-medium leading-snug">{source.title}</p>
          {source.section ? <p className="text-xs text-muted-foreground">{source.section}</p> : null}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <TrustBadge trust={source.trust} />
        <SensitivityBadge sensitivity={source.sensitivity} />
        {source.page != null ? <span className="text-xs text-muted-foreground">Page {source.page}</span> : null}
      </div>
      <Snippet source={source} />
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
        <Link href={`/knowledge/documents/${source.document_id}`} className="inline-flex items-center gap-1 text-primary hover:underline">
          <FileText className="size-3.5" aria-hidden="true" />
          Open document
        </Link>
        {source.page != null && source.filename.toLowerCase().endsWith(".pdf") ? (
          <a
            href={pdfPageUrl(source.document_id, source.page)}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-primary hover:underline"
          >
            <ExternalLink className="size-3.5" aria-hidden="true" />
            Open PDF at page {source.page}
          </a>
        ) : null}
      </div>
    </article>
  );
}

const SNIPPET = "rounded-lg bg-muted/40 px-3 py-2 text-[13px] leading-relaxed whitespace-pre-wrap";

/** Live sources carry their text; from history it's fetched (permission-checked) on demand. */
function Snippet({ source }: { source: Source }) {
  if (source.text) return <SnippetText text={source.text} />;
  return <StoredSnippet source={source} />;
}

/** A passage: plain text, with its markdown tables drawn as tables. */
export function SnippetText({ text }: { text: string }) {
  const parts = snippetParts(text);
  if (parts.every((part) => part.kind === "text")) return <p className={cn(SNIPPET, "line-clamp-[12]")}>{text}</p>;
  return (
    <div className="flex max-h-72 flex-col gap-2 overflow-auto rounded-lg bg-muted/40 px-3 py-2 text-[13px] leading-relaxed">
      {parts.map((part, i) =>
        part.kind === "text" ? (
          <p key={i} className="whitespace-pre-wrap">
            {part.text}
          </p>
        ) : (
          <table key={i} className="w-full border-collapse text-left text-xs">
            <thead>
              <tr>
                {part.header.map((cell, c) => (
                  <th key={c} scope="col" className="border-b px-2 py-1 font-medium">
                    {cell}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {part.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, c) => (
                    <td key={c} className="border-b border-border/50 px-2 py-1 align-top">
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        ),
      )}
    </div>
  );
}

function StoredSnippet({ source }: { source: Source }) {
  const { state } = useApiQuery(knowledgeKeys.chunks(source.document_id), () =>
    knowledgeApi.listChunks(source.document_id),
  );
  if (state.status === "loading") return <Skeleton className="h-16 w-full" />;
  if (state.status === "error") {
    const lost = state.error instanceof ApiError && (state.error.status === 404 || state.error.status === 403);
    return (
      <p className="flex items-center gap-1.5 rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground">
        <Lock className="size-3.5" aria-hidden="true" />
        {lost ? "You no longer have access to this document." : "Couldn't load the passage."}
      </p>
    );
  }
  const chunk = state.data.items.find((c) => c.id === source.chunk_id);
  if (!chunk) {
    return <p className="text-xs text-muted-foreground">This passage changed since; the document was re-indexed.</p>;
  }
  return <SnippetText text={chunk.text} />;
}
