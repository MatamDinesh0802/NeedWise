"use client";

import { memo, type ComponentProps } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { MarkdownImage } from "@/components/markdown/markdown-image";
import { MarkdownLink } from "@/components/markdown/markdown-link";
import { remarkCitations } from "@/lib/chat/citations";
import { cn } from "@/lib/utils";

/** react-markdown passes the hast `node`; it must not reach the DOM. */
function dom<P extends { node?: unknown }>(props: P): Omit<P, "node"> {
  const rest: Partial<P> = { ...props };
  delete rest.node;
  return rest as Omit<P, "node">;
}

export interface ChipInfo {
  title: string;
  cited: boolean;
}

/**
 * Answer markdown. Raw HTML is never rendered (react-markdown escapes it), links can't use
 * javascript: URLs, GFM tables are allowed, and [S1] / [S1, S3] become citation chips.
 * Markers inside code stay literal.
 */
export const AnswerMarkdown = memo(function AnswerMarkdown({
  text,
  sources,
  onCite,
}: {
  text: string;
  /** marker → what the chip says about it; unknown markers render muted. */
  sources: ReadonlyMap<string, ChipInfo>;
  onCite?: (marker: string) => void;
}) {
  const components: Components = {
    cite: (props: ComponentProps<"cite"> & { node?: unknown; "data-markers"?: string }) => {
      const markers = String(props["data-markers"] ?? "").split(" ").filter(Boolean);
      return (
        <span className="mx-0.5 inline-flex gap-0.5 align-baseline">
          {markers.map((marker) => (
            <CitationChip key={marker} marker={marker} info={sources.get(marker)} onCite={onCite} />
          ))}
        </span>
      );
    },
    table: (props) => (
      <div className="my-3 overflow-x-auto rounded-lg border">
        <table className="w-full text-sm" {...dom(props)} />
      </div>
    ),
    th: (props) => <th className="border-b bg-muted/50 px-3 py-1.5 text-left font-medium" {...dom(props)} />,
    td: (props) => <td className="border-b px-3 py-1.5 align-top" {...dom(props)} />,
    a: MarkdownLink,
    img: MarkdownImage,
    code: ({ className, ...props }) => (
      <code className={cn("rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]", className)} {...dom(props)} />
    ),
    pre: (props) => (
      <pre className="my-3 overflow-x-auto rounded-lg border bg-muted/50 p-3 text-xs [&_code]:bg-transparent [&_code]:p-0" {...dom(props)} />
    ),
  };

  return (
    <div
      className={cn(
        "max-w-none text-sm leading-relaxed break-words",
        "[&_p]:my-2 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0",
        "[&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-0.5",
        "[&_h1]:mt-4 [&_h1]:mb-2 [&_h1]:text-base [&_h1]:font-semibold [&_h2]:mt-4 [&_h2]:mb-2 [&_h2]:font-semibold [&_h3]:mt-3 [&_h3]:mb-1 [&_h3]:font-medium",
        "[&_blockquote]:border-l-2 [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground",
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkCitations]} components={components} skipHtml>
        {text}
      </ReactMarkdown>
    </div>
  );
});

function CitationChip({ marker, info, onCite }: { marker: string; info: ChipInfo | undefined; onCite?: (marker: string) => void }) {
  const number = marker.replace(/^S/, "");
  const label = info ? `Source ${number}: ${info.title}` : `Source ${number} (not among the retrieved sources)`;
  return (
    <button
      type="button"
      onClick={() => onCite?.(marker)}
      disabled={!info}
      aria-label={label}
      title={label}
      className={cn(
        "inline-flex h-4.5 min-w-4.5 translate-y-[-1px] items-center justify-center rounded-full border px-1 font-mono text-[10px] leading-none font-medium transition-colors",
        "focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
        info
          ? "border-primary/30 bg-primary/10 text-primary hover:bg-primary/20"
          : "cursor-not-allowed border-dashed text-muted-foreground",
      )}
    >
      {number}
    </button>
  );
}
