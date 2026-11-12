"use client";

import { ChevronDown, ChevronRight, Download, FileJson, Filter, ScrollText, ShieldCheck, X } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Fragment, Suspense, useMemo, useState } from "react";

import { VerifyResultAlert } from "@/components/admin/verify-result-alert";
import { Field } from "@/components/data/form";
import { EmptyState, ErrorAlert, TableSkeleton } from "@/components/data/states";
import { EnterpriseBadge, EnterpriseGate } from "@/components/enterprise/enterprise-gate";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ColleaguePicker } from "@/components/work/colleague-picker";
import { useColleagues } from "@/hooks/use-people";
import { useStore } from "@/hooks/use-store";
import { adminApi, type AuditEvent, type ChainResult } from "@/lib/admin/api";
import {
  activeFilterCount,
  actorLabel,
  AUDIT_ACTION_FILTERS,
  AUDIT_EXPORT_LIMIT,
  auditFilterErrors,
  auditQuery,
  EMPTY_AUDIT_FILTERS,
  filtersFromSearch,
  filtersToSearch,
  formatDetails,
  hasDetails,
  resourceLabel,
  withActor,
  withResource,
  type AuditFilterState,
} from "@/lib/admin/audit";
import { formatDateTime } from "@/lib/format";
import { createPagedList, type PagedList } from "@/lib/stores/paged-list";

const PAGE_SIZE = 50;

export function AuditSection() {
  return (
    <section className="flex flex-col gap-4" aria-labelledby="audit-heading">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id="audit-heading" className="text-lg font-semibold">
            Audit log
          </h3>
          <p className="text-sm text-muted-foreground">
            Every sign-in and change. Each event is chained to the previous one, so edits are detectable.
          </p>
        </div>
        <VerifyButton />
      </div>
      {/* The filters live in the URL (shareable, and set by links from other pages). */}
      <Suspense fallback={<TableSkeleton rows={6} />}>
        <AuditExplorer />
      </Suspense>
    </section>
  );
}

function AuditExplorer() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const colleagues = useColleagues();
  // The URL is the only source of the filters. Next keeps visited routes mounted
  // (cacheComponents), so state copied from the URL once would go stale when a link (the
  // Governance overview's "audit log") or the tab comes back here with another query.
  const search = filtersToSearch(filtersFromSearch(params));
  const applied = useMemo(() => filtersFromSearch(new URLSearchParams(search)), [search]);
  // An actor from the URL has only an id; the directory names them once it has loaded.
  const shown = useMemo<AuditFilterState>(() => {
    const known = applied.actor ? colleagues.byId.get(applied.actor.id) : undefined;
    return known && applied.actor ? { ...applied, actor: { id: known.id, display_name: known.display_name } } : applied;
  }, [applied, colleagues.byId]);

  // `search` is the filters' identity: a new list only when they change.
  const list = useMemo(() => {
    const query = auditQuery(filtersFromSearch(new URLSearchParams(search)));
    return createPagedList<AuditEvent, string>(async (cursor) => {
      const page = await adminApi.listAuditEvents({ ...query, cursor, limit: PAGE_SIZE });
      return { items: page.items, next: page.next_cursor };
    });
  }, [search]);

  function apply(next: AuditFilterState) {
    const query = filtersToSearch(next);
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  }

  return (
    <>
      <div role="group" aria-label="Show actions starting with" className="flex flex-wrap gap-1">
        {AUDIT_ACTION_FILTERS.map((filter) => {
          const active = applied.action === filter.value;
          return (
            <Button
              key={filter.value || "all"}
              size="sm"
              variant={active ? "secondary" : "ghost"}
              aria-pressed={active}
              onClick={() => apply({ ...applied, action: filter.value })}
            >
              {filter.label}
              {filter.value ? <span className="font-mono text-xs text-muted-foreground">{filter.value}</span> : null}
            </Button>
          );
        })}
      </div>

      <AuditFilterForm key={search} initial={shown} onApply={apply} />
      <ExportCard filters={applied} />
      <AuditTable list={list} filters={shown} onNarrow={apply} />
    </>
  );
}

/** Every filter the API has. Applied on submit, so typing doesn't reload the log. */
export function AuditFilterForm({ initial, onApply }: { initial: AuditFilterState; onApply: (next: AuditFilterState) => void }) {
  const [draft, setDraft] = useState<AuditFilterState>(initial);
  const [showErrors, setShowErrors] = useState(false);
  const errors = auditFilterErrors(draft);
  const count = activeFilterCount(initial);
  const set = (patch: Partial<AuditFilterState>) => setDraft((current) => ({ ...current, ...patch }));

  return (
    <form
      role="search"
      aria-label="Filter the audit log"
      noValidate
      className="flex flex-col gap-3 rounded-xl border p-4"
      onSubmit={(e) => {
        e.preventDefault();
        setShowErrors(true);
        if (Object.keys(errors).length === 0) onApply(draft);
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="Text" hint="In the action, the resource or the details.">
          {({ id, describedBy }) => (
            <Input id={id} type="search" value={draft.q} maxLength={200} onChange={(e) => set({ q: e.target.value })} aria-describedby={describedBy} placeholder="e.g. travel policy" />
          )}
        </Field>
        <Field label="Action starts with">
          {({ id }) => (
            <Input id={id} value={draft.action} maxLength={100} onChange={(e) => set({ action: e.target.value })} placeholder="e.g. knowledge.document." className="font-mono" />
          )}
        </Field>
        <div className="flex flex-col gap-1.5">
          <span className="text-sm font-medium" id="audit-actor-label">
            Person
          </span>
          {draft.actor ? (
            <div className="flex h-8 items-center justify-between gap-2 rounded-lg border px-2.5 text-sm" aria-labelledby="audit-actor-label">
              <span className="truncate">{draft.actor.display_name}</span>
              <Button type="button" variant="ghost" size="icon-xs" aria-label={`Stop filtering by ${draft.actor.display_name}`} onClick={() => set({ actor: null })}>
                <X />
              </Button>
            </div>
          ) : (
            <ColleaguePicker label="Filter by the person who acted" onPick={(person) => set({ actor: { id: person.id, display_name: person.display_name } })} />
          )}
        </div>
        <Field label="Resource type">
          {({ id }) => (
            <Input id={id} value={draft.resourceType} maxLength={100} onChange={(e) => set({ resourceType: e.target.value })} placeholder="e.g. document" className="font-mono" />
          )}
        </Field>
        <Field label="Resource ID">
          {({ id }) => <Input id={id} value={draft.resourceId} maxLength={200} onChange={(e) => set({ resourceId: e.target.value })} className="font-mono" />}
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="From" error={showErrors ? (errors.from ?? null) : null}>
            {({ id, describedBy, invalid }) => (
              <Input id={id} type="date" value={draft.from} onChange={(e) => set({ from: e.target.value })} aria-describedby={describedBy} aria-invalid={invalid || undefined} />
            )}
          </Field>
          <Field label="To (inclusive)" error={showErrors ? (errors.to ?? null) : null}>
            {({ id, describedBy, invalid }) => (
              <Input id={id} type="date" value={draft.to} onChange={(e) => set({ to: e.target.value })} aria-describedby={describedBy} aria-invalid={invalid || undefined} />
            )}
          </Field>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" size="sm">
          <Filter data-icon="inline-start" aria-hidden="true" />
          Apply filters
        </Button>
        <Button type="button" size="sm" variant="ghost" disabled={count === 0} onClick={() => onApply(EMPTY_AUDIT_FILTERS)}>
          Clear all
        </Button>
        <p className="text-sm text-muted-foreground" role="status">
          {count === 0 ? "Showing every event." : `${count} ${count === 1 ? "filter" : "filters"} applied.`}
        </p>
      </div>
    </form>
  );
}

export function ExportCard({ filters }: { filters: AuditFilterState }) {
  const filtered = activeFilterCount(filters) > 0;
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>Export</CardTitle>
        <CardDescription>
          Every export is itself recorded in the audit log (action <code className="font-mono text-xs">audit.export</code>).
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-2">
        <div className="flex flex-col items-start gap-2">
          <a href={adminApi.auditCsvUrl(auditQuery(filters))} download className={buttonVariants({ variant: "outline", size: "sm" })}>
            <Download data-icon="inline-start" aria-hidden="true" />
            {filtered ? "Download this view as CSV" : "Download as CSV"}
          </a>
          <p className="text-xs text-muted-foreground">
            The newest {AUDIT_EXPORT_LIMIT.toLocaleString("en-US")} events that match the filters, with their details as JSON. Cells a
            spreadsheet would run as a formula start with a quote.
          </p>
        </div>
        <div className="flex flex-col items-start gap-2">
          <p className="flex items-center gap-2 text-sm font-medium">
            For a SIEM
            <EnterpriseBadge feature="advanced_audit_export" />
          </p>
          <EnterpriseGate feature="advanced_audit_export">
            <a href={adminApi.auditChainUrl()} download className={buttonVariants({ variant: "outline", size: "sm" })}>
              <FileJson data-icon="inline-start" aria-hidden="true" />
              Download the full chain (JSON lines)
            </a>
            <p className="text-xs text-muted-foreground">
              Every event, oldest first, with its <code className="font-mono">prev_hash</code> and <code className="font-mono">hash</code>, so
              the receiver can verify the chain itself. Filters don&apos;t apply. See docs/enterprise.md.
            </p>
          </EnterpriseGate>
        </div>
      </CardContent>
    </Card>
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

function AuditTable({
  list,
  filters,
  onNarrow,
}: {
  list: PagedList<AuditEvent>;
  filters: AuditFilterState;
  onNarrow: (next: AuditFilterState) => void;
}) {
  const state = useStore(list);
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(new Set());
  const filtered = activeFilterCount(filters) > 0;

  if (state.status === "loading") return <TableSkeleton rows={6} />;
  if (state.status === "error") return <ErrorAlert error={state.error} onRetry={() => void list.reload()} />;
  if (state.items.length === 0) {
    return (
      <EmptyState
        icon={ScrollText}
        title={filtered ? "No events match these filters" : "No audit events yet"}
        description={filtered ? "Widen the dates or clear a filter." : "Sign-ins and changes will appear here."}
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
        <caption className="sr-only">Audit events, newest first</caption>
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
                    <TableCell colSpan={4} className="whitespace-normal">
                      <div className="mb-2 flex flex-wrap items-center gap-2">
                        {event.actor ? (
                          <Button size="xs" variant="outline" onClick={() => onNarrow(withActor(filters, event))}>
                            Only {event.actor.display_name}
                          </Button>
                        ) : null}
                        {event.resource_type ? (
                          <Button size="xs" variant="outline" onClick={() => onNarrow(withResource(filters, event))}>
                            Only this {event.resource_type}
                          </Button>
                        ) : null}
                        {event.ip ? <span className="text-xs text-muted-foreground">IP address: {event.ip}</span> : null}
                      </div>
                      {hasDetails(event) ? (
                        <pre className="max-h-72 overflow-auto rounded-md border bg-background p-3 font-mono text-xs whitespace-pre-wrap">
                          {formatDetails(event.details)}
                        </pre>
                      ) : (
                        <p className="text-xs text-muted-foreground">No details.</p>
                      )}
                    </TableCell>
                  </TableRow>
                ) : null}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>

      {state.moreError ? <ErrorAlert error={state.moreError} onRetry={() => void list.loadMore()} /> : null}
      <p className="text-center text-xs text-muted-foreground" role="status">
        {state.items.length.toLocaleString("en-US")} {state.items.length === 1 ? "event" : "events"} shown
        {state.hasMore ? "." : filtered ? ": every match." : ": the beginning of the log."}
      </p>
      {state.hasMore ? (
        <div className="flex justify-center">
          <Button variant="outline" onClick={() => void list.loadMore()} disabled={state.loadingMore}>
            {state.loadingMore ? "Loading…" : "Load older"}
          </Button>
        </div>
      ) : null}
    </>
  );
}
