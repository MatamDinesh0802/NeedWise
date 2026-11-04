"use client";

import { ChevronDown, ChevronRight, ScrollText, ShieldCheck } from "lucide-react";
import { Fragment, useMemo, useState } from "react";

import { VerifyResultAlert } from "@/components/admin/verify-result-alert";
import { EmptyState, ErrorAlert, TableSkeleton } from "@/components/data/states";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useStore } from "@/hooks/use-store";
import { adminApi, type AuditEvent, type ChainResult } from "@/lib/admin/api";
import {
  actorLabel,
  AUDIT_ACTION_FILTERS,
  formatDetails,
  hasDetails,
  resourceLabel,
  type AuditActionFilter,
} from "@/lib/admin/audit";
import { formatDateTime } from "@/lib/format";
import { createPagedList, type PagedList } from "@/lib/stores/paged-list";

const PAGE_SIZE = 50;

export function AuditSection() {
  const [action, setAction] = useState<AuditActionFilter>("");
  const list = useMemo(
    () =>
      createPagedList<AuditEvent, number>(async (beforeSeq) => {
        const page = await adminApi.listAuditEvents({ action, before_seq: beforeSeq, limit: PAGE_SIZE });
        return { items: page.items, next: page.next_before_seq };
      }),
    [action],
  );

  return (
    <section className="flex flex-col gap-4" aria-labelledby="audit-heading">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id="audit-heading" className="text-lg font-semibold">
            Audit log
          </h3>
          <p className="text-sm text-muted-foreground">
            Every sign-in and administrative change. Each event is chained to the previous one, so edits are detectable.
          </p>
        </div>
        <VerifyButton />
      </div>

      <div role="group" aria-label="Filter by action" className="flex flex-wrap gap-1">
        {AUDIT_ACTION_FILTERS.map((filter) => (
          <Button
            key={filter.value || "all"}
            size="sm"
            variant={action === filter.value ? "secondary" : "ghost"}
            aria-pressed={action === filter.value}
            onClick={() => setAction(filter.value)}
          >
            {filter.label}
            {filter.value ? <span className="font-mono text-xs text-muted-foreground">{filter.value}</span> : null}
          </Button>
        ))}
      </div>

      <AuditTable list={list} filtered={action !== ""} />
    </section>
  );
}

function VerifyButton() {
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<ChainResult | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function verify() {
    setPending(true);
    setError(null);
    try {
      setResult(await adminApi.verifyAuditChain());
    } catch (err) {
      setResult(null);
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex w-full flex-col items-end gap-2 sm:w-auto">
      <Button variant="outline" onClick={() => void verify()} disabled={pending}>
        <ShieldCheck data-icon="inline-start" />
        {pending ? "Verifying…" : "Verify integrity"}
      </Button>
      {error ? <ErrorAlert error={error} className="sm:max-w-md" /> : null}
      {result ? <VerifyResultAlert result={result} /> : null}
    </div>
  );
}

function AuditTable({ list, filtered }: { list: PagedList<AuditEvent>; filtered: boolean }) {
  const state = useStore(list);
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(new Set());

  if (state.status === "loading") return <TableSkeleton rows={6} />;
  if (state.status === "error") return <ErrorAlert error={state.error} onRetry={() => void list.reload()} />;
  if (state.items.length === 0) {
    return (
      <EmptyState
        icon={ScrollText}
        title={filtered ? "No events with this action" : "No audit events yet"}
        description={filtered ? "Try another filter." : "Sign-ins and admin changes will appear here."}
      />
    );
  }

  const toggle = (seq: number) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(seq)) next.delete(seq);
      else next.add(seq);
      return next;
    });

  return (
    <>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-8">
              <span className="sr-only">Details</span>
            </TableHead>
            <TableHead>Time</TableHead>
            <TableHead>Actor</TableHead>
            <TableHead>Action</TableHead>
            <TableHead>Resource</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {state.items.map((event) => {
            const open = expanded.has(event.seq);
            const detailsId = `audit-details-${event.seq}`;
            return (
              <Fragment key={event.seq}>
                <TableRow>
                  <TableCell>
                    {hasDetails(event) || event.ip ? (
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-expanded={open}
                        aria-controls={detailsId}
                        aria-label={open ? `Hide details of event ${event.seq}` : `Show details of event ${event.seq}`}
                        onClick={() => toggle(event.seq)}
                      >
                        {open ? <ChevronDown /> : <ChevronRight />}
                      </Button>
                    ) : null}
                  </TableCell>
                  <TableCell className="whitespace-nowrap">
                    <span>{formatDateTime(event.occurred_at)}</span>
                    <span className="ml-2 font-mono text-xs text-muted-foreground">#{event.seq}</span>
                  </TableCell>
                  <TableCell>{actorLabel(event)}</TableCell>
                  <TableCell className="font-mono text-xs">{event.action}</TableCell>
                  <TableCell className="max-w-64 truncate text-muted-foreground" title={resourceLabel(event)}>
                    {resourceLabel(event)}
                  </TableCell>
                </TableRow>
                {open ? (
                  <TableRow id={detailsId} className="bg-muted/30 hover:bg-muted/30">
                    <TableCell />
                    <TableCell colSpan={4}>
                      {event.ip ? <p className="mb-1 text-xs text-muted-foreground">IP address: {event.ip}</p> : null}
                      <pre className="max-h-72 overflow-auto rounded-md border bg-background p-3 font-mono text-xs whitespace-pre-wrap">
                        {formatDetails(event.details)}
                      </pre>
                    </TableCell>
                  </TableRow>
                ) : null}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>

      {state.moreError ? <ErrorAlert error={state.moreError} onRetry={() => void list.loadMore()} /> : null}
      {state.hasMore ? (
        <div className="flex justify-center">
          <Button variant="outline" onClick={() => void list.loadMore()} disabled={state.loadingMore}>
            {state.loadingMore ? "Loading…" : "Load older"}
          </Button>
        </div>
      ) : (
        <p className="text-center text-xs text-muted-foreground">Beginning of the log.</p>
      )}
    </>
  );
}
