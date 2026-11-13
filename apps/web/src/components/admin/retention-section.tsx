"use client";

import { Eye, ShieldCheck, Trash2 } from "lucide-react";
import { useId, useState } from "react";

import { Field, SimpleSelect } from "@/components/data/form";
import { ErrorAlert, TableSkeleton } from "@/components/data/states";
import { EnterpriseBadge, EnterpriseGate } from "@/components/enterprise/enterprise-gate";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApiQuery } from "@/hooks/use-api-query";
import {
  anyKept,
  CONFIRM_WORD,
  confirmMatches,
  DOCUMENT_ACTIONS,
  documentCount,
  documentDraftChanged,
  documentDraftErrors,
  documentDraftFromRules,
  documentDraftToRules,
  documentRetentionApi,
  documentRetentionKeys,
  keptSummary,
  type DocumentAction,
  type DocumentDraft,
  type DocumentPreview,
  type DocumentRules,
  type DocumentRun,
  countsSummary,
  countText,
  draftChanged,
  draftErrors,
  draftFromPolicy,
  draftToBody,
  keepsEverything,
  lastRunSummary,
  RETENTION_KINDS,
  retentionApi,
  retentionKeys,
  type RetentionDraft,
  type RetentionPolicy,
  type RetentionPreview,
  type RetentionRun,
} from "@/lib/admin/retention";
import { formatDate, formatDateTime } from "@/lib/format";
import { knowledgeApi, knowledgeKeys } from "@/lib/knowledge/api";
import { getQueryCache } from "@/lib/stores/query-cache";

/** Admin > Retention (Enterprise). The API answers 402 without a license; the gate only explains. */
export function RetentionSection() {
  return (
    <section className="flex flex-col gap-4" aria-labelledby="retention-heading">
      <div>
        <h3 id="retention-heading" className="flex flex-wrap items-center gap-2 text-lg font-semibold">
          Retention
          <EnterpriseBadge feature="retention_policies" />
        </h3>
        <p className="text-sm text-muted-foreground">
          How long data is kept before it is deleted, and each collection&apos;s documents before they are deleted or archived. A daily
          task applies the saved policy and rules; you can preview them and run them now. Anything under a legal hold is kept.
        </p>
      </div>
      <Alert role="note">
        <ShieldCheck aria-hidden="true" />
        <AlertTitle>The audit log is never deleted</AlertTitle>
        <AlertDescription>
          Audit events form a hash chain: deleting old ones would break the verification of every later event, and the
          database role the app runs as cannot delete them. Each run is itself recorded in the audit log.
        </AlertDescription>
      </Alert>
      <EnterpriseGate feature="retention_policies">
        <div className="flex flex-col gap-4">
          <RetentionLoader />
          <DocumentRetentionLoader />
        </div>
      </EnterpriseGate>
    </section>
  );
}

function RetentionLoader() {
  const { state, refetch } = useApiQuery(retentionKeys.policy, retentionApi.get);
  if (state.status === "loading") return <TableSkeleton rows={4} />;
  if (state.status === "error") return <ErrorAlert error={state.error} onRetry={() => void refetch()} />;
  return <RetentionEditor key={state.data.updated_at ?? "new"} policy={state.data} />;
}

export function RetentionEditor({ policy, initialPreview = null }: { policy: RetentionPolicy; initialPreview?: RetentionPreview | null }) {
  const [draft, setDraft] = useState<RetentionDraft>(() => draftFromPolicy(policy));
  const [showErrors, setShowErrors] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<unknown>(null);
  const [preview, setPreview] = useState<{ result: RetentionPreview; draft: boolean } | null>(
    initialPreview ? { result: initialPreview, draft: false } : null,
  );
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [running, setRunning] = useState(false);
  const errors = draftErrors(draft, policy.minimum_days);
  const valid = Object.keys(errors).length === 0;
  const changed = draftChanged(draft, policy.updated_at ? policy : null);
  const saved = policy.updated_at !== null;
  const formId = useId();

  function update(next: RetentionDraft) {
    setDraft(next);
    setPreview(null); // a preview describes the form as it was
  }

  async function save() {
    setShowErrors(true);
    if (!valid) return;
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await retentionApi.save(draftToBody(draft));
      getQueryCache().setData<RetentionPolicy>(retentionKeys.policy, () => updated);
    } catch (err) {
      setSaveError(err);
    } finally {
      setSaving(false);
    }
  }

  async function runPreview() {
    setShowErrors(true);
    if (!valid) return;
    setPreviewing(true);
    setPreviewError(null);
    try {
      const result = await retentionApi.preview(changed ? draftToBody(draft) : undefined);
      setPreview({ result, draft: changed });
    } catch (err) {
      setPreviewError(err);
    } finally {
      setPreviewing(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Card size="sm">
        <CardHeader>
          <CardTitle>Policy</CardTitle>
          <CardDescription>
            Days to keep each kind of data, at least {policy.minimum_days}. Leave a field empty to keep that data forever.
            {policy.updated_at
              ? ` Saved ${formatDateTime(policy.updated_at)}${policy.updated_by ? ` by ${policy.updated_by.display_name}` : ""}.`
              : " No policy is saved yet, so nothing is deleted."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form
            id={formId}
            className="flex flex-col gap-4"
            noValidate
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <fieldset className="grid gap-4 sm:grid-cols-2" disabled={saving}>
              <legend className="sr-only">Days to keep</legend>
              {RETENTION_KINDS.map(({ kind, label, description }) => (
                <Field key={kind} label={`${label} (days)`} hint={description} error={showErrors ? (errors[kind] ?? null) : null}>
                  {({ id, describedBy, invalid }) => (
                    <Input
                      id={id}
                      inputMode="numeric"
                      value={draft[kind]}
                      placeholder="Keep forever"
                      onChange={(e) => update({ ...draft, [kind]: e.target.value })}
                      aria-describedby={describedBy}
                      aria-invalid={invalid || undefined}
                      className="max-w-48"
                    />
                  )}
                </Field>
              ))}
            </fieldset>
            {saveError ? <ErrorAlert error={saveError} /> : null}
            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" disabled={saving || !changed}>
                {saving ? "Saving…" : "Save policy"}
              </Button>
              <Button type="button" variant="outline" onClick={() => void runPreview()} disabled={previewing}>
                <Eye data-icon="inline-start" aria-hidden="true" />
                {previewing ? "Counting…" : changed ? "Preview these changes" : "Preview"}
              </Button>
              <p className="text-sm text-muted-foreground" role="status">
                {changed ? "Unsaved changes." : "Everything is saved."}
              </p>
            </div>
          </form>
        </CardContent>
      </Card>

      {previewError ? <ErrorAlert error={previewError} /> : null}
      {preview ? <PreviewResult preview={preview.result} draft={preview.draft} /> : null}

      <Card size="sm">
        <CardHeader>
          <CardTitle>Run now</CardTitle>
          <CardDescription>{lastRunSummary(policy)}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          <div>
            <Button
              type="button"
              variant="destructive"
              onClick={() => setRunning(true)}
              disabled={!saved || changed || keepsEverything(policy)}
            >
              <Trash2 data-icon="inline-start" aria-hidden="true" />
              Run the saved policy…
            </Button>
          </div>
          {changed ? (
            <p className="text-sm text-muted-foreground">Save your changes first: a run applies the saved policy.</p>
          ) : !saved || keepsEverything(policy) ? (
            <p className="text-sm text-muted-foreground">The saved policy keeps everything, so a run would delete nothing.</p>
          ) : null}
        </CardContent>
      </Card>

      <Dialog open={running} onOpenChange={setRunning}>
        <DialogContent className="sm:max-w-lg">{running ? <RunForm onDone={() => setRunning(false)} /> : null}</DialogContent>
      </Dialog>
    </div>
  );
}

export function PreviewResult({ preview, draft }: { preview: RetentionPreview; draft: boolean }) {
  const kept = anyKept(preview.kept_under_hold);
  return (
    <section aria-labelledby="retention-preview-heading" className="flex flex-col gap-2 rounded-xl border p-4" aria-live="polite">
      <h4 id="retention-preview-heading" className="font-medium">
        {draft ? "If you saved these changes, a run now would delete" : "A run now would delete"}: {countsSummary(preview.would_delete)}
      </h4>
      <p className="text-sm text-muted-foreground">
        Counted {formatDateTime(preview.as_of)}. Nothing was deleted.
        {kept ? ` A legal hold keeps ${countsSummary(preview.kept_under_hold)} that would otherwise go.` : ""}
      </p>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Data</TableHead>
            <TableHead>Deleted when older than</TableHead>
            <TableHead className="text-right">Would delete</TableHead>
            {kept ? <TableHead className="text-right">Kept (legal hold)</TableHead> : null}
          </TableRow>
        </TableHeader>
        <TableBody>
          {RETENTION_KINDS.map(({ kind, label }) => (
            <TableRow key={kind}>
              <TableCell>{label}</TableCell>
              <TableCell className="text-muted-foreground">{preview.cutoffs[kind] ? formatDate(preview.cutoffs[kind]) : "Kept forever"}</TableCell>
              <TableCell className="text-right tabular-nums">{preview.would_delete[kind].toLocaleString("en-US")}</TableCell>
              {kept ? <TableCell className="text-right tabular-nums">{(preview.kept_under_hold?.[kind] ?? 0).toLocaleString("en-US")}</TableCell> : null}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </section>
  );
}

export function RunForm({ onDone, initialResult = null }: { onDone: () => void; initialResult?: RetentionRun | null }) {
  const [typed, setTyped] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<RetentionRun | null>(initialResult);
  const ok = confirmMatches(typed);

  async function run() {
    if (!ok) return;
    setPending(true);
    setError(null);
    try {
      setResult(await retentionApi.run());
      await getQueryCache().invalidate(retentionKeys.policy);
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  if (result) {
    return (
      <>
        <DialogHeader>
          <DialogTitle>Retention run finished</DialogTitle>
          <DialogDescription>
            Deleted {countsSummary(result.deleted)}
            {result.deleted.meeting_recordings ? `; ${result.files_deleted.toLocaleString("en-US")} recording files removed from storage` : ""}.
            {anyKept(result.kept_under_hold) ? ` A legal hold kept ${countsSummary(result.kept_under_hold)}.` : ""} The run is recorded in
            the audit log.
          </DialogDescription>
        </DialogHeader>
        <ul className="list-disc pl-5 text-sm">
          {RETENTION_KINDS.map(({ kind }) => (
            <li key={kind}>{countText(kind, result.deleted[kind])}</li>
          ))}
        </ul>
        <DialogFooter>
          <Button type="button" onClick={onDone}>
            Close
          </Button>
        </DialogFooter>
      </>
    );
  }

  return (
    <form
      className="contents"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        void run();
      }}
    >
      <DialogHeader>
        <DialogTitle>Delete expired data now?</DialogTitle>
        <DialogDescription>
          Everything older than the saved policy allows is deleted for good: conversations with their messages, Insights
          questions, recording files and read notifications. This can&apos;t be undone.
        </DialogDescription>
      </DialogHeader>
      <Field label={`Type “${CONFIRM_WORD}” to confirm`}>
        {({ id }) => (
          <Input id={id} value={typed} autoFocus autoComplete="off" spellCheck={false} onChange={(e) => setTyped(e.target.value)} disabled={pending} />
        )}
      </Field>
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" variant="destructive" disabled={!ok || pending}>
          {pending ? "Deleting…" : "Delete expired data"}
        </Button>
      </DialogFooter>
    </form>
  );
}

// --- Documents, per collection ---------------------------------------------------------------------

function DocumentRetentionLoader() {
  const rules = useApiQuery(documentRetentionKeys.rules, documentRetentionApi.get);
  const collections = useApiQuery(knowledgeKeys.adminCollections, knowledgeApi.listAllCollections);
  if (rules.state.status === "loading" || collections.state.status === "loading") return <TableSkeleton rows={4} />;
  if (rules.state.status === "error") return <ErrorAlert error={rules.state.error} onRetry={() => void rules.refetch()} />;
  if (collections.state.status === "error") return <ErrorAlert error={collections.state.error} onRetry={() => void collections.refetch()} />;
  const saved = rules.state.data;
  const key = saved.rules.map((r) => `${r.collection_id}:${r.days}:${r.action}:${r.updated_at}`).join("|") || "none";
  return (
    <DocumentRetentionEditor
      key={key}
      rules={saved}
      collections={collections.state.data.items.map((c) => ({ id: c.id, name: c.name, documents: c.document_count }))}
    />
  );
}

export function DocumentRetentionEditor({
  rules,
  collections,
  initialPreview = null,
}: {
  rules: DocumentRules;
  collections: { id: string; name: string; documents: number }[];
  initialPreview?: DocumentPreview | null;
}) {
  const [draft, setDraft] = useState<DocumentDraft>(() =>
    documentDraftFromRules(
      rules.rules,
      collections.map((c) => c.id),
    ),
  );
  const [showErrors, setShowErrors] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<unknown>(null);
  const [preview, setPreview] = useState<{ result: DocumentPreview; draft: boolean } | null>(
    initialPreview ? { result: initialPreview, draft: false } : null,
  );
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [running, setRunning] = useState(false);
  const errors = documentDraftErrors(draft, rules.minimum_days);
  const valid = Object.keys(errors).length === 0;
  const changed = documentDraftChanged(draft, rules.rules);
  const lastRun = new Map(rules.rules.map((r) => [r.collection_id, r]));

  function update(id: string, row: Partial<DocumentDraft[string]>) {
    setDraft({ ...draft, [id]: { ...draft[id], ...row } });
    setPreview(null);
  }

  async function save() {
    setShowErrors(true);
    if (!valid) return;
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await documentRetentionApi.save(documentDraftToRules(draft));
      getQueryCache().setData<DocumentRules>(documentRetentionKeys.rules, () => updated);
    } catch (err) {
      setSaveError(err);
    } finally {
      setSaving(false);
    }
  }

  async function runPreview() {
    setShowErrors(true);
    if (!valid) return;
    setPreviewing(true);
    setPreviewError(null);
    try {
      const result = await documentRetentionApi.preview(changed ? documentDraftToRules(draft) : undefined);
      setPreview({ result, draft: changed });
    } catch (err) {
      setPreviewError(err);
    } finally {
      setPreviewing(false);
    }
  }

  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>Documents, by collection</CardTitle>
        <CardDescription>
          Delete or archive the documents of a collection that have not been updated for a number of days (at least{" "}
          {rules.minimum_days}). New content, a review or a restore counts as an update. Documents under a legal hold and
          official documents are never touched; deleting also leaves synced documents and meeting transcripts alone. Archived
          documents stay in the Knowledge Hub, out of search and answers, until an editor restores them.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <form
          className="flex flex-col gap-4"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <div className="overflow-x-auto rounded-xl border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Collection</TableHead>
                  <TableHead>Not updated for (days)</TableHead>
                  <TableHead>Then</TableHead>
                  <TableHead>Last run</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {collections.map((c) => {
                  const row = draft[c.id] ?? { days: "", action: "archive" as const };
                  const error = showErrors ? errors[c.id] : undefined;
                  const run = lastRun.get(c.id);
                  return (
                    <TableRow key={c.id} className="align-top">
                      <TableCell className="whitespace-normal">
                        {c.name}
                        <p className="text-xs text-muted-foreground">{documentCount(c.documents)}</p>
                      </TableCell>
                      <TableCell>
                        <Input
                          inputMode="numeric"
                          value={row.days}
                          placeholder="Keep"
                          aria-label={`${c.name}: days without an update`}
                          aria-invalid={error ? true : undefined}
                          onChange={(e) => update(c.id, { days: e.target.value })}
                          disabled={saving}
                          className="max-w-28"
                        />
                        {error ? <p className="mt-1 text-xs text-destructive">{error}</p> : null}
                      </TableCell>
                      <TableCell>
                        <SimpleSelect<DocumentAction>
                          ariaLabel={`${c.name}: what happens`}
                          value={row.action}
                          options={DOCUMENT_ACTIONS}
                          onChange={(action) => update(c.id, { action })}
                          disabled={saving || row.days.trim() === ""}
                          size="sm"
                        />
                      </TableCell>
                      <TableCell className="text-sm whitespace-normal text-muted-foreground">
                        {run?.last_run_at
                          ? `${formatDate(run.last_run_at)}: ${run.last_run.action === "delete" ? "deleted" : "archived"} ${documentCount(run.last_run.count ?? 0)}`
                          : "—"}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          {saveError ? <ErrorAlert error={saveError} /> : null}
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" disabled={saving || !changed}>
              {saving ? "Saving…" : "Save rules"}
            </Button>
            <Button type="button" variant="outline" onClick={() => void runPreview()} disabled={previewing}>
              <Eye data-icon="inline-start" aria-hidden="true" />
              {previewing ? "Counting…" : changed ? "Preview these changes" : "Preview"}
            </Button>
            <Button type="button" variant="destructive" onClick={() => setRunning(true)} disabled={changed || rules.rules.length === 0}>
              <Trash2 data-icon="inline-start" aria-hidden="true" />
              Run the saved rules…
            </Button>
            <p className="text-sm text-muted-foreground" role="status">
              {changed ? "Unsaved changes: a run applies the saved rules." : rules.rules.length === 0 ? "No rules: every document is kept." : "Everything is saved."}
            </p>
          </div>
        </form>
        {previewError ? <ErrorAlert error={previewError} /> : null}
        {preview ? <DocumentPreviewResult preview={preview.result} draft={preview.draft} /> : null}
        <Dialog open={running} onOpenChange={setRunning}>
          <DialogContent className="sm:max-w-lg">{running ? <DocumentRunForm onDone={() => setRunning(false)} /> : null}</DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  );
}

export function DocumentPreviewResult({ preview, draft }: { preview: DocumentPreview; draft: boolean }) {
  const kept = keptSummary(preview.kept);
  return (
    <section aria-labelledby="document-preview-heading" className="flex flex-col gap-2 rounded-xl border p-4" aria-live="polite">
      <h4 id="document-preview-heading" className="font-medium">
        {draft ? "If you saved these rules, a run now would" : "A run now would"} delete {documentCount(preview.would_delete)} and archive{" "}
        {documentCount(preview.would_archive)}
      </h4>
      <p className="text-sm text-muted-foreground">
        Counted {formatDateTime(preview.as_of)}. Nothing was changed.{kept ? ` Kept although not updated: ${kept}.` : ""}
      </p>
      {preview.collections.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Collection</TableHead>
              <TableHead>Not updated since</TableHead>
              <TableHead className="text-right">Would</TableHead>
              <TableHead>Kept</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {preview.collections.map((c) => (
              <TableRow key={c.collection_id}>
                <TableCell>{c.collection}</TableCell>
                <TableCell className="text-muted-foreground">{formatDate(c.cutoff)}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {c.action === "delete" ? "Delete" : "Archive"} {c.count.toLocaleString("en-US")}
                </TableCell>
                <TableCell className="text-sm whitespace-normal text-muted-foreground">{keptSummary(c.kept) ?? "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}
    </section>
  );
}

export function DocumentRunForm({ onDone, initialResult = null }: { onDone: () => void; initialResult?: DocumentRun | null }) {
  const [typed, setTyped] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<DocumentRun | null>(initialResult);
  const ok = confirmMatches(typed);

  async function run() {
    if (!ok) return;
    setPending(true);
    setError(null);
    try {
      setResult(await documentRetentionApi.run());
      await getQueryCache().invalidate(documentRetentionKeys.rules);
      await getQueryCache().invalidate(knowledgeKeys.all);
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  if (result) {
    const kept = keptSummary(result.kept);
    return (
      <>
        <DialogHeader>
          <DialogTitle>Document retention run finished</DialogTitle>
          <DialogDescription>
            Deleted {documentCount(result.deleted)} and archived {documentCount(result.archived)}.{kept ? ` Kept: ${kept}.` : ""} Each
            document and the run are recorded in the audit log.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button type="button" onClick={onDone}>
            Close
          </Button>
        </DialogFooter>
      </>
    );
  }

  return (
    <form
      className="contents"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        void run();
      }}
    >
      <DialogHeader>
        <DialogTitle>Apply the document rules now?</DialogTitle>
        <DialogDescription>
          Documents not updated within their collection&apos;s period are deleted for good, with their versions, search entries and
          files, or archived, as each rule says. Deleting can&apos;t be undone.
        </DialogDescription>
      </DialogHeader>
      <Field label={`Type “${CONFIRM_WORD}” to confirm`}>
        {({ id }) => (
          <Input id={id} value={typed} autoFocus autoComplete="off" spellCheck={false} onChange={(e) => setTyped(e.target.value)} disabled={pending} />
        )}
      </Field>
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" variant="destructive" disabled={!ok || pending}>
          {pending ? "Applying…" : "Apply the rules"}
        </Button>
      </DialogFooter>
    </form>
  );
}
