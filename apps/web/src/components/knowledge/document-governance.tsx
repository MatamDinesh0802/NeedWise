"use client";

import { BadgeCheck, CalendarCheck, Clock, ShieldOff, UserRoundPen } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Field, SimpleSelect } from "@/components/data/form";
import { ErrorAlert } from "@/components/data/states";
import { TrustBadge } from "@/components/knowledge/badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { ColleaguePicker } from "@/components/work/colleague-picker";
import { invalidateQueries } from "@/hooks/use-api-query";
import { knowledgeApi, knowledgeKeys, type DocumentDetail, type Trust } from "@/lib/knowledge/api";
import {
  DEMOTE_TO,
  governanceKeys,
  NOTE_MAX,
  noteError,
  officialBlocker,
  officialRequestSummary,
  personLabel,
  reviewBadge,
  reviewState,
  reviewSummary,
} from "@/lib/knowledge/governance";
import { toast } from "@/lib/stores/toasts";
import { cn } from "@/lib/utils";

type DialogKind = "owner" | "review" | "official" | "demote" | null;

async function refresh(doc: DocumentDetail) {
  await Promise.all([
    invalidateQueries(knowledgeKeys.document(doc.id)),
    invalidateQueries(knowledgeKeys.documents(doc.collection_id)),
    invalidateQueries(governanceKeys.overview),
  ]);
}

/** Who answers for the document, its review cycle and its official status (docs/governance.md). */
export function DocumentGovernance({ doc, meId, now }: { doc: DocumentDetail; meId: string | null; now?: number }) {
  const [open, setOpen] = useState<DialogKind>(null);
  const state = reviewState(doc, now);
  const badge = reviewBadge(doc, now);
  const blocker = officialBlocker(doc, doc.can_edit);
  const close = () => setOpen(null);

  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>Ownership and review</CardTitle>
        <CardDescription>Who answers for this document, when it was last checked, and whether answers treat it as official.</CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="flex flex-col divide-y text-sm">
          <Row
            label="Owner"
            action={
              doc.can_change_owner ? (
                <Button variant="outline" size="sm" onClick={() => setOpen("owner")}>
                  <UserRoundPen data-icon="inline-start" aria-hidden="true" />
                  Change owner
                </Button>
              ) : null
            }
          >
            {doc.owner ? (
              <span>
                {doc.owner.display_name}
                {doc.owner.id === meId ? <span className="text-muted-foreground"> (you)</span> : null}
              </span>
            ) : (
              <span className="text-muted-foreground">No owner. Nobody is reminded to review it.</span>
            )}
          </Row>

          <Row
            label="Review"
            action={
              doc.can_review ? (
                <Button variant="outline" size="sm" onClick={() => setOpen("review")}>
                  <CalendarCheck data-icon="inline-start" aria-hidden="true" />
                  Mark reviewed
                </Button>
              ) : null
            }
          >
            <span className="flex flex-wrap items-center gap-2">
              <span>{reviewSummary(doc, meId)}</span>
              {badge ? (
                <Badge
                  variant="secondary"
                  className={cn(
                    state === "overdue"
                      ? "bg-amber-500/15 text-amber-800 dark:text-amber-300"
                      : "bg-sky-500/10 text-sky-700 dark:text-sky-400",
                  )}
                >
                  <Clock data-icon="inline-start" aria-hidden="true" />
                  {badge}
                </Badge>
              ) : null}
            </span>
          </Row>

          <Row
            label="Official status"
            action={
              doc.trust === "official" && doc.can_edit ? (
                <Button variant="outline" size="sm" onClick={() => setOpen("demote")}>
                  <ShieldOff data-icon="inline-start" aria-hidden="true" />
                  Remove official status
                </Button>
              ) : blocker === null ? (
                <Button variant="outline" size="sm" onClick={() => setOpen("official")}>
                  <BadgeCheck data-icon="inline-start" aria-hidden="true" />
                  Request official status
                </Button>
              ) : null
            }
          >
            <div className="flex flex-col gap-1.5">
              <span className="flex flex-wrap items-center gap-2">
                <TrustBadge trust={doc.trust} />
                {doc.official_request ? (
                  <Badge variant="secondary" className="bg-amber-500/15 text-amber-800 dark:text-amber-300">
                    <Clock data-icon="inline-start" aria-hidden="true" />
                    Official status requested
                  </Badge>
                ) : null}
              </span>
              {doc.official_request ? (
                <>
                  <span className="text-muted-foreground">{officialRequestSummary(doc.official_request, meId)}</span>
                  {doc.official_request.note ? (
                    <blockquote className="border-l-2 pl-3 text-muted-foreground italic">{doc.official_request.note}</blockquote>
                  ) : null}
                </>
              ) : doc.trust === "official" ? (
                <span className="text-muted-foreground">Approved: answers rank it first and prefer it when sources disagree.</span>
              ) : doc.can_edit ? (
                <span className="text-muted-foreground">
                  Official needs an approval from the collection&apos;s owner, or an administrator.
                </span>
              ) : null}
            </div>
          </Row>
        </dl>
      </CardContent>

      <Dialog open={open !== null} onOpenChange={(next) => !next && close()}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
          {open === "owner" ? <ChangeOwnerForm doc={doc} onDone={close} /> : null}
          {open === "review" ? <MarkReviewedForm doc={doc} onDone={close} /> : null}
          {open === "official" ? <RequestOfficialForm doc={doc} meId={meId} onDone={close} /> : null}
          {open === "demote" ? <DemoteForm doc={doc} onDone={close} /> : null}
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function Row({ label, action, children }: { label: string; action: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-start sm:justify-between">
      <div className="flex min-w-0 flex-col gap-1">
        <dt className="text-xs text-muted-foreground">{label}</dt>
        <dd>{children}</dd>
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

/** Pending flag, error and a submit wrapper shared by the forms. */
function useSubmit(onSuccess: () => void) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function run(action: () => Promise<unknown>) {
    setPending(true);
    setError(null);
    try {
      await action();
      setPending(false);
      onSuccess();
    } catch (err) {
      setPending(false);
      setError(err);
    }
  }
  return { pending, error, run };
}

function ChangeOwnerForm({ doc, onDone }: { doc: DocumentDetail; onDone: () => void }) {
  const { pending, error, run } = useSubmit(onDone);
  return (
    <>
      <DialogHeader>
        <DialogTitle>Change the owner</DialogTitle>
        <DialogDescription>
          The owner answers for the content and is reminded when a review is due. They must be able to open this collection.
          {doc.owner ? ` Now: ${doc.owner.display_name}.` : " It has no owner now."}
        </DialogDescription>
      </DialogHeader>
      <ColleaguePicker
        label="Find the new owner"
        autoFocus
        disabled={pending}
        exclude={new Set(doc.owner ? [doc.owner.id] : [])}
        onPick={(person) =>
          void run(async () => {
            await knowledgeApi.setOwner(doc.id, person.id);
            await refresh(doc);
            toast({ title: "Owner changed", description: `${person.display_name} now owns “${doc.title}”.` });
          })
        }
      />
      {pending ? <p className="text-sm text-muted-foreground" role="status">Saving…</p> : null}
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
      </DialogFooter>
    </>
  );
}

function MarkReviewedForm({ doc, onDone }: { doc: DocumentDetail; onDone: () => void }) {
  const [note, setNote] = useState("");
  const { pending, error, run } = useSubmit(onDone);
  const problem = noteError(note, { required: false });
  return (
    <form
      className="contents"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        if (problem) return;
        void run(async () => {
          await knowledgeApi.markReviewed(doc.id, note);
          await refresh(doc);
          toast({ title: "Marked reviewed", description: `“${doc.title}” is reviewed as of today.` });
        });
      }}
    >
      <DialogHeader>
        <DialogTitle>Mark “{doc.title}” reviewed</DialogTitle>
        <DialogDescription>
          You confirm the content is still correct. The next review is scheduled from today, and the review task, if there is
          one, is closed. This is recorded in the audit log.
        </DialogDescription>
      </DialogHeader>
      <Field label="What did you check? (optional)" error={problem}>
        {({ id, describedBy, invalid }) => (
          <Textarea
            id={id}
            rows={3}
            value={note}
            maxLength={NOTE_MAX + 1}
            onChange={(e) => setNote(e.target.value)}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            disabled={pending}
            placeholder="e.g. Checked against the 2026 travel rates"
          />
        )}
      </Field>
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? "Saving…" : "Mark reviewed"}
        </Button>
      </DialogFooter>
    </form>
  );
}

function RequestOfficialForm({ doc, meId, onDone }: { doc: DocumentDetail; meId: string | null; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [showErrors, setShowErrors] = useState(false);
  const { pending, error, run } = useSubmit(onDone);
  const problem = noteError(note, { required: true });
  return (
    <form
      className="contents"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        setShowErrors(true);
        if (problem) return;
        void run(async () => {
          const updated = await knowledgeApi.requestOfficial(doc.id, note.trim());
          await refresh(doc);
          const approvers = updated.official_request?.approvers.map((a) => personLabel(a, meId)) ?? [];
          toast({
            title: "Official status requested",
            description: approvers.length ? `Sent to ${approvers.join(", ")} for approval.` : "Sent for approval.",
          });
        });
      }}
    >
      <DialogHeader>
        <DialogTitle>Request official status</DialogTitle>
        <DialogDescription>
          Official documents rank first in answers and win when sources disagree, so a second person approves it: the
          collection&apos;s owner, or an administrator when you are the owner or there is none. Nobody approves their own
          request. Until then its trust stays as it is.
        </DialogDescription>
      </DialogHeader>
      <Field label="Why should it be official?" hint="The approver reads this." error={showErrors ? problem : null}>
        {({ id, describedBy, invalid }) => (
          <Textarea
            id={id}
            rows={3}
            value={note}
            required
            maxLength={NOTE_MAX + 1}
            autoFocus
            onChange={(e) => setNote(e.target.value)}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            disabled={pending}
            placeholder="e.g. This is the policy HR signed off on 1 November"
          />
        )}
      </Field>
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? "Sending…" : "Send request"}
        </Button>
      </DialogFooter>
    </form>
  );
}

function DemoteForm({ doc, onDone }: { doc: DocumentDetail; onDone: () => void }) {
  const [trust, setTrust] = useState<Exclude<Trust, "official">>("reference");
  const [note, setNote] = useState("");
  const { pending, error, run } = useSubmit(onDone);
  const problem = noteError(note, { required: false });
  return (
    <form
      className="contents"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        if (problem) return;
        void run(async () => {
          await knowledgeApi.updateLabels(doc.id, { trust, ...(note.trim() ? { note: note.trim() } : {}) });
          await refresh(doc);
          toast({ title: "No longer official", description: `“${doc.title}” is now ${trust}.` });
        });
      }}
    >
      <DialogHeader>
        <DialogTitle>Remove official status</DialogTitle>
        <DialogDescription>
          This takes effect at once: answers stop preferring this document. It is recorded in the audit log
          {doc.owner ? `, and ${doc.owner.display_name} (the owner) is told` : ""}. Making it official again needs a new approval.
        </DialogDescription>
      </DialogHeader>
      <Field label="New trust level">
        {({ id }) => <SimpleSelect id={id} value={trust} onChange={setTrust} options={DEMOTE_TO} disabled={pending} />}
      </Field>
      <Field label="Why? (optional)" hint="The owner sees this note." error={problem}>
        {({ id, describedBy, invalid }) => (
          <Textarea
            id={id}
            rows={2}
            value={note}
            maxLength={NOTE_MAX + 1}
            onChange={(e) => setNote(e.target.value)}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            disabled={pending}
            placeholder="e.g. Superseded by the 2027 policy"
          />
        )}
      </Field>
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" variant="destructive" disabled={pending}>
          {pending ? "Saving…" : "Remove official status"}
        </Button>
      </DialogFooter>
    </form>
  );
}
