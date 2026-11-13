"use client";

import { Gavel, LockKeyhole, Plus, Unlock } from "lucide-react";
import { useId, useState } from "react";

import { PersonPicker } from "@/components/admin/person-picker";
import { Field, SimpleSelect } from "@/components/data/form";
import { EmptyState, ErrorAlert, TableSkeleton } from "@/components/data/states";
import { EnterpriseBadge, EnterpriseGate } from "@/components/enterprise/enterprise-gate";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { formatDate } from "@/lib/format";
import { knowledgeApi, knowledgeKeys } from "@/lib/knowledge/api";
import {
  byMatter,
  coversSummary,
  emptyHoldDraft,
  HOLD_TARGETS,
  holdDraftErrors,
  holdDraftToBody,
  legalHoldKeys,
  legalHoldsApi,
  MATTER_MAX,
  REASON_MAX,
  targetInfo,
  type HoldDraft,
  type HoldStatus,
  type HoldTargetType,
  type LegalHold,
} from "@/lib/admin/legal-holds";

/** Admin > Legal holds (Enterprise). The API answers 402 without a license; the gate only explains. */
export function LegalHoldsSection() {
  return (
    <section className="flex flex-col gap-4" aria-labelledby="legal-holds-heading">
      <div>
        <h3 id="legal-holds-heading" className="flex flex-wrap items-center gap-2 text-lg font-semibold">
          Legal holds
          <EnterpriseBadge feature="legal_hold" />
        </h3>
        <p className="text-sm text-muted-foreground">
          Keep what a legal matter needs from being deleted: a person&apos;s conversations, meetings and Insights questions, a
          collection, a document or a meeting.
        </p>
      </div>
      <Alert role="note">
        <LockKeyhole aria-hidden="true" />
        <AlertTitle>Nothing a hold covers can be deleted</AlertTitle>
        <AlertDescription>
          Not by the people who could otherwise delete it, not when it disappears from a synced source, not by a retention run,
          and not when the organization is deleted without an explicit override. Every refused deletion, every hold and every
          release is recorded in the audit log.
        </AlertDescription>
      </Alert>
      <EnterpriseGate feature="legal_hold">
        <LegalHoldsLoader />
      </EnterpriseGate>
    </section>
  );
}

function LegalHoldsLoader() {
  const [showReleased, setShowReleased] = useState(false);
  const status: HoldStatus = showReleased ? "all" : "active";
  const { state, refetch } = useApiQuery(legalHoldKeys.list(status), () => legalHoldsApi.list(status));
  const switchId = useId();
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Switch id={switchId} checked={showReleased} onCheckedChange={setShowReleased} />
        <label htmlFor={switchId} className="text-sm">
          Show released holds
        </label>
      </div>
      {state.status === "loading" ? (
        <TableSkeleton rows={3} />
      ) : state.status === "error" ? (
        <ErrorAlert error={state.error} onRetry={() => void refetch()} />
      ) : (
        <LegalHoldsView holds={state.data.items} />
      )}
    </div>
  );
}

async function refreshHolds() {
  await invalidateQueries(legalHoldKeys.all);
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function LegalHoldsView({ holds }: { holds: LegalHold[] }) {
  const [placing, setPlacing] = useState(false);
  const [releasing, setReleasing] = useState<LegalHold | null>(null);
  const active = holds.filter((h) => h.active);
  const released = holds.filter((h) => !h.active);

  return (
    <div className="flex flex-col gap-4">
      <div>
        <Button type="button" onClick={() => setPlacing(true)}>
          <Plus data-icon="inline-start" aria-hidden="true" />
          Place a hold…
        </Button>
      </div>
      {active.length === 0 ? (
        <EmptyState icon={Gavel} title="No active holds" description="Everything can be deleted as usual." />
      ) : (
        byMatter(active).map(({ matter, holds: items }) => (
          <section key={matter} aria-label={matter} className="flex flex-col gap-2">
            <h4 className="font-medium">{matter}</h4>
            <HoldTable holds={items} onRelease={setReleasing} />
          </section>
        ))
      )}
      {released.length > 0 ? (
        <section aria-labelledby="released-holds" className="flex flex-col gap-2">
          <h4 id="released-holds" className="font-medium">
            Released
          </h4>
          <HoldTable holds={released} />
        </section>
      ) : null}

      <Dialog open={placing} onOpenChange={setPlacing}>
        <DialogContent className="sm:max-w-lg">{placing ? <PlaceHoldForm onDone={() => setPlacing(false)} /> : null}</DialogContent>
      </Dialog>
      <Dialog open={releasing !== null} onOpenChange={(open) => (open ? null : setReleasing(null))}>
        <DialogContent className="sm:max-w-lg">
          {releasing ? <ReleaseHoldForm hold={releasing} onDone={() => setReleasing(null)} /> : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function HoldTable({ holds, onRelease }: { holds: LegalHold[]; onRelease?: (hold: LegalHold) => void }) {
  return (
    <div className="overflow-x-auto rounded-xl border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Holds</TableHead>
            <TableHead>{onRelease ? "Keeps now" : "Covers"}</TableHead>
            <TableHead>Why</TableHead>
            <TableHead>{onRelease ? "Placed" : "Placed and released"}</TableHead>
            {onRelease ? <TableHead className="sr-only">Actions</TableHead> : null}
          </TableRow>
        </TableHeader>
        <TableBody>
          {holds.map((hold) => (
            <TableRow key={hold.id} className="align-top">
              <TableCell className="max-w-72 whitespace-normal">
                <span className="flex flex-col gap-1">
                  <span>{hold.target.label}</span>
                  <span className="flex flex-wrap gap-1">
                    <Badge variant="secondary">{targetInfo(hold.target.type).label}</Badge>
                    {hold.active ? null : <Badge variant="outline">Released</Badge>}
                  </span>
                </span>
              </TableCell>
              <TableCell className="max-w-56 whitespace-normal">{coversSummary(hold.covers)}</TableCell>
              <TableCell className="max-w-72 text-sm whitespace-pre-wrap text-muted-foreground">
                {hold.reason}
                {hold.release_reason ? `\nReleased: ${hold.release_reason}` : ""}
              </TableCell>
              <TableCell className="text-sm whitespace-nowrap">
                {formatDate(hold.placed_at)}
                {hold.placed_by ? <p className="text-xs text-muted-foreground">by {hold.placed_by.display_name}</p> : null}
                {hold.released_at ? (
                  <p className="text-xs text-muted-foreground">
                    released {formatDate(hold.released_at)}
                    {hold.released_by ? ` by ${hold.released_by.display_name}` : ""}
                  </p>
                ) : null}
              </TableCell>
              {onRelease ? (
                <TableCell className="text-right">
                  <Button type="button" variant="outline" size="sm" onClick={() => onRelease(hold)}>
                    <Unlock data-icon="inline-start" aria-hidden="true" />
                    Release…
                  </Button>
                </TableCell>
              ) : null}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export function PlaceHoldForm({ onDone, initial }: { onDone: () => void; initial?: HoldDraft }) {
  const [draft, setDraft] = useState<HoldDraft>(initial ?? emptyHoldDraft());
  const [picked, setPicked] = useState<string | null>(null);
  const [showErrors, setShowErrors] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const errors = holdDraftErrors(draft);
  const valid = Object.keys(errors).length === 0;
  const info = targetInfo(draft.target_type);

  async function place() {
    setShowErrors(true);
    if (!valid) return;
    setPending(true);
    setError(null);
    try {
      await legalHoldsApi.place(holdDraftToBody(draft));
      await refreshHolds();
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      className="contents"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        void place();
      }}
    >
      <DialogHeader>
        <DialogTitle>Place a legal hold</DialogTitle>
        <DialogDescription>
          From now on nothing the hold covers can be deleted, by anyone or anything, until an administrator releases it.
        </DialogDescription>
      </DialogHeader>
      <Field label="Matter" hint="The case or investigation. Holds are grouped by it." error={showErrors ? (errors.matter ?? null) : null}>
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            value={draft.matter}
            maxLength={MATTER_MAX}
            placeholder="e.g. Smith v. Acme"
            onChange={(e) => setDraft({ ...draft, matter: e.target.value })}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            disabled={pending}
          />
        )}
      </Field>
      <Field label="What to hold" hint={info.keeps}>
        {({ id, describedBy }) => (
          <SimpleSelect<HoldTargetType>
            id={id}
            describedBy={describedBy}
            value={draft.target_type}
            options={HOLD_TARGETS.map((t) => ({ value: t.type, label: t.label }))}
            onChange={(type) => {
              setDraft({ ...draft, target_type: type, target: "" });
              setPicked(null);
            }}
            disabled={pending}
          />
        )}
      </Field>
      <TargetField
        draft={draft}
        picked={picked}
        error={showErrors ? (errors.target ?? null) : null}
        disabled={pending}
        onPick={(target, label) => {
          setDraft({ ...draft, target });
          setPicked(label);
        }}
      />
      <Field label="Why" hint="Recorded in the audit log with the hold." error={showErrors ? (errors.reason ?? null) : null}>
        {({ id, describedBy, invalid }) => (
          <Textarea
            id={id}
            value={draft.reason}
            maxLength={REASON_MAX}
            rows={3}
            onChange={(e) => setDraft({ ...draft, reason: e.target.value })}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            disabled={pending}
          />
        )}
      </Field>
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? "Placing…" : "Place hold"}
        </Button>
      </DialogFooter>
    </form>
  );
}

function TargetField({
  draft,
  picked,
  error,
  disabled,
  onPick,
}: {
  draft: HoldDraft;
  picked: string | null;
  error: string | null;
  disabled: boolean;
  onPick: (target: string, label: string | null) => void;
}) {
  const info = targetInfo(draft.target_type);
  if (info.picker === "person") {
    return (
      <div className="flex flex-col gap-1.5">
        <p className="text-sm font-medium">Person</p>
        {picked ? (
          <div className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm">
            <span>{picked}</span>
            <Button type="button" variant="ghost" size="sm" onClick={() => onPick("", null)} disabled={disabled}>
              Change
            </Button>
          </div>
        ) : (
          <PersonPicker label="Find the person" exclude={new Set()} disabled={disabled} onPick={(user) => onPick(user.id, user.display_name)} />
        )}
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
      </div>
    );
  }
  if (info.picker === "collection") {
    return <CollectionChoice value={draft.target} error={error} disabled={disabled} onPick={(id) => onPick(id, null)} />;
  }
  return (
    <Field label={`${capitalize(info.label.replace(/^An? /, ""))}: id or link`} hint={`Copy it from ${info.where}.`} error={error}>
      {({ id, describedBy, invalid }) => (
        <Input
          id={id}
          value={draft.target}
          spellCheck={false}
          autoComplete="off"
          onChange={(e) => onPick(e.target.value, null)}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          disabled={disabled}
        />
      )}
    </Field>
  );
}

function CollectionChoice({
  value,
  error,
  disabled,
  onPick,
}: {
  value: string;
  error: string | null;
  disabled: boolean;
  onPick: (id: string) => void;
}) {
  const { state, refetch } = useApiQuery(knowledgeKeys.adminCollections, knowledgeApi.listAllCollections);
  if (state.status === "loading") return <TableSkeleton rows={1} />;
  if (state.status === "error") return <ErrorAlert error={state.error} onRetry={() => void refetch()} />;
  const options = [{ value: "", label: "Choose a collection" }, ...state.data.items.map((c) => ({ value: c.id, label: c.name }))];
  return (
    <Field label="Collection" error={error}>
      {({ id, describedBy, invalid }) => (
        <SimpleSelect<string> id={id} describedBy={describedBy} invalid={invalid} value={value} options={options} onChange={onPick} disabled={disabled} />
      )}
    </Field>
  );
}

export function ReleaseHoldForm({ hold, onDone }: { hold: LegalHold; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const [showError, setShowError] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const problem = reason.trim() ? null : "Say why the hold ends. The audit log records it.";

  async function release() {
    setShowError(true);
    if (problem) return;
    setPending(true);
    setError(null);
    try {
      await legalHoldsApi.release(hold.id, reason.trim());
      await refreshHolds();
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      className="contents"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        void release();
      }}
    >
      <DialogHeader>
        <DialogTitle>Release this hold?</DialogTitle>
        <DialogDescription>
          {hold.matter}: {hold.target.label}. Once released, what it kept ({coversSummary(hold.covers)}) can be deleted again, unless
          another hold covers it. The hold stays listed as released.
        </DialogDescription>
      </DialogHeader>
      <Field label="Why it ends" error={showError ? problem : null}>
        {({ id, describedBy, invalid }) => (
          <Textarea
            id={id}
            value={reason}
            rows={3}
            maxLength={REASON_MAX}
            onChange={(e) => setReason(e.target.value)}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            disabled={pending}
          />
        )}
      </Field>
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" variant="destructive" disabled={pending}>
          {pending ? "Releasing…" : "Release hold"}
        </Button>
      </DialogFooter>
    </form>
  );
}
