"use client";

import { TriangleAlert } from "lucide-react";
import { memo, useMemo, type ComponentProps } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { MarkdownImage } from "@/components/markdown/markdown-image";
import { MarkdownLink } from "@/components/markdown/markdown-link";
import type { VerifiedNumber } from "@/lib/analytics/api";
import { remarkNarrative, UNVERIFIED_EXPLANATION } from "@/lib/analytics/narrative";
import { cn } from "@/lib/utils";

/** react-markdown passes the hast `node`; it must not reach the DOM. */
function dom<P extends { node?: unknown }>(props: P): Omit<P, "node"> {
  const rest: Partial<P> = { ...props };
  delete rest.node;
  return rest as Omit<P, "node">;
}

/** Element id of a query's section within one answer, for [Qn] links. */
export function queryAnchor(answerId: string, queryId: string): string {
  return `answer-${answerId}-${queryId}`;
}

/**
 * The answer's narrative (markdown; raw HTML is never rendered). [Q1] markers link to that query
 * below; each figure the API checked is marked: verified quietly, unverified with a warning.
 */
export const Narrative = memo(function Narrative({
  text,
  numbers,
  answerId,
  queryIds,
}: {
  text: string;
  numbers: readonly VerifiedNumber[];
  answerId: string;
  /** Queries that exist in this answer; markers for others render as plain text. */
  queryIds: readonly string[];
}) {
  const plugins = useMemo(() => [remarkGfm, remarkNarrative(numbers)], [numbers]);
  const components: Components = {
    cite: (props: ComponentProps<"cite"> & { node?: unknown; "data-queries"?: string }) => {
      const ids = String(props["data-queries"] ?? "").split(" ").filter(Boolean);
      return (
        <span className="mx-0.5 inline-flex gap-0.5 align-baseline">
          {ids.map((id) =>
            queryIds.includes(id) ? (
              <a
                key={id}
                href={`#${queryAnchor(answerId, id)}`}
                aria-label={`Query ${id.replace(/^Q/, "")}: see the SQL and results`}
                className="inline-flex h-4.5 translate-y-[-1px] items-center rounded-full border border-primary/30 bg-primary/10 px-1.5 font-mono text-[10px] leading-none font-medium text-primary no-underline hover:bg-primary/20 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
              >
                {id}
              </a>
            ) : (
              <span key={id} className="font-mono text-[10px] text-muted-foreground">
                [{id}]
              </span>
            ),
          )}
        </span>
      );
    },
    data: (props: ComponentProps<"data"> & { node?: unknown; "data-index"?: string }) => {
      const entry = numbers[Number(props["data-index"])];
      const { children, value } = dom(props);
      if (!entry) return <>{children}</>;
      if (entry.verified) {
        return (
          <data
            value={value}
            data-verified="true"
            title={entry.query ? `Verified: matches ${entry.query}'s results` : "Verified against the query results"}
            className="underline decoration-emerald-600/40 decoration-dotted underline-offset-[3px] dark:decoration-emerald-400/50"
          >
            {children}
            <span className="sr-only"> (verified{entry.query ? ` in ${entry.query}` : ""})</span>
          </data>
        );
      }
      return (
        <data
          value={value}
          data-verified="false"
          title={`Unverified. ${UNVERIFIED_EXPLANATION}`}
          className="rounded-sm bg-amber-500/15 px-0.5 text-amber-900 ring-1 ring-amber-500/40 dark:text-amber-200"
        >
          <TriangleAlert className="mr-0.5 inline size-3 -translate-y-px" aria-hidden="true" />
          {children}
          <span className="sr-only"> (not verified: check it against the tables)</span>
        </data>
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
    code: ({ className, ...props }) => <code className={cn("rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]", className)} {...dom(props)} />,
  };

  return (
    <div
      className={cn(
        "max-w-none text-sm leading-relaxed break-words",
        "[&_p]:my-2 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0",
        "[&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-0.5",
        "[&_h1]:mt-4 [&_h1]:mb-2 [&_h1]:text-base [&_h1]:font-semibold [&_h2]:mt-4 [&_h2]:mb-2 [&_h2]:font-semibold [&_h3]:mt-3 [&_h3]:mb-1 [&_h3]:font-medium",
      )}
    >
      <ReactMarkdown remarkPlugins={plugins} components={components} skipHtml>
        {text}
      </ReactMarkdown>
    </div>
  );
});
