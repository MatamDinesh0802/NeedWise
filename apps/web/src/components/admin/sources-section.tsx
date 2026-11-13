"use client";

import { CircleAlert, CircleCheck, FileCode, Globe, HardDrive, Info, KeyRound, LoaderCircle, MessagesSquare, Plus, RefreshCw, Trash2, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/data/confirm-dialog";
import { Field, SimpleSelect } from "@/components/data/form";
import { EmptyState, ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { usePolling } from "@/hooks/use-polling";
import {
  anySyncing,
  configFieldKey,
  CREDENTIAL_LABELS,
  credentialFields,
  credentialsPayload,
  emptySourceForm,
  formFromSource,
  intervalLabel,
  INTERVALS,
  KIND_HINTS,
  KIND_LABELS,
  SOURCE_KEY_MAX,
  sourcesApi,
  sourcesKeys,
  STATUS_LABELS,
  statsSummary,
  toCreatePayload,
  toggledSource,
  toUpdatePayload,
  validateSourceForm,
  withSourceName,
  type Source,
  type SourceForm,
  type SourceFormErrors,
  type SourceKind,
  type SourceStatus,
} from "@/lib/admin/sources";
import { errorMessage, fieldErrors } from "@/lib/error-messages";
import { formatDate, formatDateTime, formatRelative } from "@/lib/format";
import { knowledgeApi, knowledgeKeys, type AdminCollection } from "@/lib/knowledge/api";
import { toast } from "@/lib/stores/toasts";
import { cn } from "@/lib/utils";
import { detailErrors } from "@/lib/validation-detail";

const KIND_ICONS: Record<SourceKind, LucideIcon> = {
  web: Globe,
  github: FileCode,
  slack: MessagesSquare,
  gdrive: HardDrive,
};

const STATUS_STYLE: Record<SourceStatus, string> = {
  idle: "bg-muted text-muted-foreground",
  syncing: "bg-sky-500/15 text-sky-800 dark:text-sky-300",
  ok: "bg-emerald-500/15 text-emerald-800 dark:text-emerald-300",
  error: "bg-destructive/10 text-destructive",
};

export function SourcesSection() {
  const sources = useApiQuery(sourcesKeys.list, sourcesApi.list);
  const collections = useApiQuery(knowledgeKeys.adminCollections, knowledgeApi.listAllCollections);
  const [editing, setEditing] = useState<Source | "new" | null>(null);
  const [credentials, setCredentials] = useState<Source | null>(null);
  const [deleting, setDeleting] = useState<Source | null>(null);
  const [watching, setWatching] = useState(false);

  const list = sources.state.status === "success" ? sources.state.data : [];
  usePolling(sourcesKeys.list, watching || anySyncing(list), 2500);
  // After "Sync now" the worker may take a moment to pick it up: keep looking for a minute.
  useEffect(() => {
    if (!watching) return;
    const timer = setTimeout(() => setWatching(false), 60_000);
    return () => clearTimeout(timer);
  }, [watching]);

  const byKey = new Map<string, AdminCollection>(
    collections.state.status === "success" ? collections.state.data.items.map((c) => [c.key, c]) : [],
  );

  return (
    <section className="flex flex-col gap-4" aria-labelledby="sources-heading">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id="sources-heading" className="flex items-center gap-2 text-lg font-semibold">
            Sources
            <RefreshingHint active={sources.state.status === "success" && sources.state.refreshing} />
          </h3>
          <p className="text-sm text-muted-foreground">Keep a collection in sync with a website, a GitHub repository, Slack channels or a Drive folder.</p>
        </div>
        <Button onClick={() => setEditing("new")}>
          <Plus data-icon="inline-start" />
          New source
        </Button>
      </div>

      <Alert>
        <Info aria-hidden="true" />
        <AlertDescription>Everything synced from a source gets its collection&apos;s access rules: people see it only if they can see that collection.</AlertDescription>
      </Alert>

      <Suspense fallback={null}>
        <OAuthResult />
      </Suspense>

      {sources.state.status === "loading" ? (
        <TableSkeleton rows={2} />
      ) : sources.state.status === "error" ? (
        <ErrorAlert error={sources.state.error} onRetry={() => void sources.refetch()} />
      ) : list.length === 0 ? (
        <EmptyState icon={Globe} title="No sources yet" description="Documents you upload stay as they are; a source keeps a collection up to date by itself." />
      ) : (
        <ul className="flex flex-col gap-3">
          {list.map((source) => (
            <SourceCard
              key={source.id}
              source={source}
              collection={byKey.get(source.collection)}
              onEdit={() => setEditing(source)}
              onCredentials={() => setCredentials(source)}
              onDelete={() => setDeleting(source)}
              onSynced={() => setWatching(true)}
            />
          ))}
        </ul>
      )}

      <StaleDocuments />

      <Dialog open={editing !== null} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl">
          {editing !== null ? (
            <SourceFormView
              key={editing === "new" ? "new" : editing.key}
              source={editing === "new" ? null : editing}
              collections={collections.state.status === "success" ? collections.state.data.items : []}
              collectionsError={collections.state.status === "error" ? collections.state.error : null}
              onDone={(created) => {
                setEditing(null);
                if (created) setWatching(true);
              }}
            />
          ) : null}
        </DialogContent>
      </Dialog>

      <Dialog open={credentials !== null} onOpenChange={(open) => !open && setCredentials(null)}>
        <DialogContent className="sm:max-w-md">{credentials ? <CredentialsForm source={credentials} onDone={() => setCredentials(null)} /> : null}</DialogContent>
      </Dialog>

      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={deleting ? `Delete ${deleting.name}?` : "Delete source?"}
        description={
          deleting ? (
            <>
              This also deletes the {deleting.documents} {deleting.documents === 1 ? "document" : "documents"} it synced into{" "}
              <span className="font-medium">{byKey.get(deleting.collection)?.name ?? deleting.collection}</span>, from the knowledge base
              and from search. It can&apos;t be undone.
            </>
          ) : null
        }
        confirmLabel="Delete source and documents"
        onConfirm={async () => {
          if (!deleting) return;
          await sourcesApi.remove(deleting.key);
          await invalidateQueries(sourcesKeys.all);
          toast({ title: `${deleting.name} deleted`, description: "Its documents are gone from search too." });
        }}
      />
    </section>
  );
}

/** After the Google consent screen the API redirects to /admin/sources?oauth=connected|failed. */
function OAuthResult() {
  const params = useSearchParams();
  const result = params.get("oauth");
  if (result === "connected") {
    return (
      <Alert className="border-emerald-500/40">
        <CircleCheck aria-hidden="true" />
        <AlertTitle>Google Drive connected</AlertTitle>
        <AlertDescription>{params.get("source") ? `“${params.get("source")}” can sync now.` : "The source can sync now."}</AlertDescription>
      </Alert>
    );
  }
  if (result === "failed") {
    return (
      <Alert variant="destructive">
        <CircleAlert aria-hidden="true" />
        <AlertTitle>Google Drive wasn&apos;t connected</AlertTitle>
        <AlertDescription>The consent was cancelled or expired, or it came from another session. Try “Connect Google Drive” again.</AlertDescription>
      </Alert>
    );
  }
  return null;
}

function SourceCard({
  source,
  collection,
  onEdit,
  onCredentials,
  onDelete,
  onSynced,
}: {
  source: Source;
  collection: AdminCollection | undefined;
  onEdit: () => void;
  onCredentials: () => void;
  onDelete: () => void;
  onSynced: () => void;
}) {
  const [busy, setBusy] = useState<"sync" | "toggle" | "google" | null>(null);
  const [googleError, setGoogleError] = useState<unknown>(null);
  const Icon = KIND_ICONS[source.kind] ?? Globe;
  const stats = statsSummary(source.stats);
  const needsCredentials = source.kind !== "gdrive" && source.needs.length > 0;

  async function run(kind: "sync" | "toggle", action: () => Promise<unknown>, done: string) {
    setBusy(kind);
    try {
      await action();
      await invalidateQueries(sourcesKeys.all);
      toast({ title: done, description: source.name });
    } catch (error) {
      const message = errorMessage(error);
      toast({ title: message.title, description: message.description, tone: "error" });
    } finally {
      setBusy(null);
    }
  }

  async function connectGoogle() {
    setBusy("google");
    setGoogleError(null);
    try {
      await sourcesApi.checkGoogleStart(source.key);
      window.location.assign(sourcesApi.googleStartUrl(source.key)); // on to Google's consent screen
    } catch (error) {
      setGoogleError(error);
      setBusy(null);
    }
  }

  return (
    <li className={cn("flex flex-col gap-3 rounded-xl border bg-card p-4", !source.enabled && "opacity-75")}>
      <div className="flex flex-wrap items-start gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border bg-muted/40">
          <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-medium">
            {source.name} <span className="font-mono text-xs font-normal text-muted-foreground">{source.key}</span>
          </p>
          <p className="text-sm text-muted-foreground">
            {KIND_LABELS[source.kind]} → {collection ? <Link href={`/knowledge/collections/${collection.id}`} className="underline-offset-2 hover:underline">{collection.name}</Link> : source.collection}
            {" · "}
            {source.documents} {source.documents === 1 ? "document" : "documents"} · {intervalLabel(source.interval_minutes).toLowerCase()}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Badge variant="secondary" className={STATUS_STYLE[source.status]}>
            {source.status === "syncing" ? <LoaderCircle className="animate-spin" data-icon="inline-start" aria-hidden="true" /> : null}
            {STATUS_LABELS[source.status] ?? source.status}
          </Badge>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            {source.enabled ? "Enabled" : "Paused"}
            <Switch
              checked={source.enabled}
              disabled={busy !== null}
              aria-label={`${source.enabled ? "Pause" : "Resume"} ${source.name}`}
              onCheckedChange={(enabled) => void run("toggle", () => sourcesApi.update(source.key, toggledSource(source, enabled)), enabled ? "Syncing resumed" : "Syncing paused")}
            />
          </label>
        </div>
      </div>

      <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-3">
        <div>
          <dt className="text-muted-foreground">Last sync</dt>
          <dd title={formatDateTime(source.last_sync_at)}>{source.last_sync_at ? formatRelative(source.last_sync_at) : "Never"}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Last success</dt>
          <dd title={formatDateTime(source.last_success_at)}>{source.last_success_at ? formatRelative(source.last_success_at) : "Never"}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Last run</dt>
          <dd>{stats ?? "—"}</dd>
        </div>
      </dl>

      {source.last_error ? (
        <p className="rounded-lg bg-destructive/10 px-3 py-2 text-xs break-words text-destructive">
          <CircleAlert className="mr-1 inline size-3.5 align-[-3px]" aria-hidden="true" />
          {source.last_error}
        </p>
      ) : null}
      {googleError ? <GoogleError error={googleError} /> : null}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          {source.kind === "gdrive" ? (
            source.has_credentials ? "Google account connected" : "Not connected to Google yet"
          ) : needsCredentials ? (
            <>
              <KeyRound className="mr-1 inline size-3 align-[-2px]" aria-hidden="true" />
              {source.has_credentials ? "Credentials stored (encrypted, never shown)" : source.kind === "github" ? "No token (public repositories only)" : "No credentials yet"}
            </>
          ) : (
            "No credentials needed"
          )}
        </p>
        <div className="flex flex-wrap gap-2">
          {source.kind === "gdrive" ? (
            <Button variant="outline" size="sm" disabled={busy !== null} onClick={() => void connectGoogle()}>
              {busy === "google" ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <HardDrive data-icon="inline-start" />}
              {source.has_credentials ? "Reconnect Google Drive" : "Connect Google Drive"}
            </Button>
          ) : null}
          {needsCredentials ? (
            <Button variant="ghost" size="sm" onClick={onCredentials}>
              <KeyRound data-icon="inline-start" />
              Credentials
            </Button>
          ) : null}
          <Button variant="ghost" size="sm" onClick={onDelete} aria-label={`Delete ${source.name}`}>
            <Trash2 data-icon="inline-start" />
            Delete
          </Button>
          <Button variant="outline" size="sm" onClick={onEdit}>
            Edit
          </Button>
          <Button
            size="sm"
            disabled={busy !== null || source.status === "syncing"}
            onClick={() =>
              void run(
                "sync",
                async () => {
                  await sourcesApi.sync(source.key);
                  onSynced();
                },
                "Sync started",
              )
            }
          >
            {busy === "sync" || source.status === "syncing" ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <RefreshCw data-icon="inline-start" />}
            Sync now
          </Button>
        </div>
      </div>
    </li>
  );
}

function GoogleError({ error }: { error: unknown }) {
  const code = errorMessage(error).code;
  if (code === "oauth_not_configured") {
    return (
      <Alert>
        <Info aria-hidden="true" />
        <AlertTitle>Google sign-in isn&apos;t set up on this server</AlertTitle>
        <AlertDescription>
          An administrator of the deployment needs to create a Google OAuth client and set <code>GOOGLE_CLIENT_ID</code> and{" "}
          <code>GOOGLE_CLIENT_SECRET</code>, with the redirect URI <code>…/api/v1/oauth/google/callback</code> (see docs/connectors.md).
        </AlertDescription>
      </Alert>
    );
  }
  return <ErrorAlert error={error} />;
}

function SourceFormView({
  source,
  collections,
  collectionsError,
  onDone,
}: {
  source: Source | null;
  collections: AdminCollection[];
  collectionsError: unknown;
  onDone: (created: boolean) => void;
}) {
  const creating = source === null;
  const [form, setForm] = useState<SourceForm>(() => (source ? formFromSource(source) : emptySourceForm()));
  const [showErrors, setShowErrors] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const errors = validateSourceForm(form, { creating });
  const server: SourceFormErrors = Object.fromEntries(fieldErrors(error).map((f) => [f.field, f.message]));
  for (const e of detailErrors(error, ["invalid_config"])) {
    const key = configFieldKey(form.kind, e.field);
    server[key] = server[key] ? `${server[key]} ${e.message}` : e.message;
  }
  const code = errorMessage(error).code;
  if (code === "slug_taken") server.key = "A source with this key already exists.";
  if (code === "unknown_collection") server.collection = "That collection no longer exists.";
  const shown = (field: string) => (showErrors ? (errors[field] ?? null) : null) ?? server[field] ?? null;
  const patch = (change: Partial<SourceForm>) => setForm((f) => ({ ...f, ...change }));
  const handled = Boolean(server.key || server.collection || server.config || Object.keys(server).some((k) => k.includes(".")));

  async function save() {
    setShowErrors(true);
    if (Object.keys(errors).length > 0) return;
    setPending(true);
    setError(null);
    try {
      const saved = creating ? await sourcesApi.create(toCreatePayload(form)) : await sourcesApi.update(form.key, toUpdatePayload(form));
      if (creating && saved.enabled && saved.kind !== "gdrive") await sourcesApi.sync(saved.key).catch(() => undefined); // first sync right away
      await invalidateQueries(sourcesKeys.all);
      toast({
        title: creating ? `${saved.name} created` : `${saved.name} saved`,
        description: creating ? (saved.kind === "gdrive" ? "Now connect Google Drive to start syncing." : "The first sync has started.") : undefined,
      });
      onDone(creating);
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  const text = (field: string, label: string, value: string, onChange: (v: string) => void, opts: { hint?: string; placeholder?: string; mono?: boolean } = {}) => (
    <Field label={label} hint={opts.hint} error={shown(field)}>
      {({ id, describedBy, invalid }) => (
        <Input id={id} value={value} placeholder={opts.placeholder} className={opts.mono ? "font-mono" : undefined} spellCheck={false} onChange={(e) => onChange(e.target.value)} aria-describedby={describedBy} aria-invalid={invalid || undefined} />
      )}
    </Field>
  );
  const lines = (field: string, label: string, value: string, onChange: (v: string) => void, opts: { hint?: string; placeholder?: string } = {}) => (
    <Field label={label} hint={opts.hint} error={shown(field)}>
      {({ id, describedBy, invalid }) => (
        <Textarea id={id} rows={3} value={value} placeholder={opts.placeholder} className="font-mono text-xs" spellCheck={false} onChange={(e) => onChange(e.target.value)} aria-describedby={describedBy} aria-invalid={invalid || undefined} />
      )}
    </Field>
  );

  return (
    <form
      className="contents"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <DialogHeader>
        <DialogTitle>{creating ? "New source" : `Edit ${source.name}`}</DialogTitle>
        <DialogDescription>Everything synced from this source gets the collection&apos;s access rules.</DialogDescription>
      </DialogHeader>

      <div className="flex flex-col gap-4">
        {creating ? (
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-sm font-medium">Kind</legend>
            <div className="grid gap-2 sm:grid-cols-4" role="radiogroup" aria-label="Kind">
              {(Object.keys(KIND_LABELS) as SourceKind[]).map((kind) => {
                const KindIcon = KIND_ICONS[kind];
                const active = form.kind === kind;
                return (
                  <button
                    key={kind}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    onClick={() => patch({ kind, credentials: {} })}
                    className={cn(
                      "flex flex-col items-start gap-1 rounded-lg border p-2.5 text-left text-sm transition-colors hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
                      active && "border-primary bg-muted/60",
                    )}
                  >
                    <KindIcon className="size-4 text-muted-foreground" aria-hidden="true" />
                    <span className="font-medium">{KIND_LABELS[kind]}</span>
                  </button>
                );
              })}
            </div>
            <p className="text-xs text-muted-foreground">{KIND_HINTS[form.kind]}</p>
          </fieldset>
        ) : null}

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name" error={shown("name")}>
            {({ id, describedBy, invalid }) => (
              <Input id={id} maxLength={120} value={form.name} onChange={(e) => setForm((f) => withSourceName(f, e.target.value))} aria-describedby={describedBy} aria-invalid={invalid || undefined} />
            )}
          </Field>
          <Field label="Key" hint={creating ? "Can't be changed later." : "Can't be changed."} error={creating ? shown("key") : null}>
            {({ id, describedBy, invalid }) => (
              <Input id={id} className="font-mono" spellCheck={false} autoCapitalize="none" maxLength={SOURCE_KEY_MAX} value={form.key} disabled={!creating} onChange={(e) => patch({ key: e.target.value, keyEdited: true })} aria-describedby={describedBy} aria-invalid={invalid || undefined} />
            )}
          </Field>
        </div>

        <Field label="Collection" hint={creating ? "Synced documents go here and get its access rules." : "Can't be changed: create a new source to sync somewhere else."} error={creating ? shown("collection") : null}>
          {({ id }) =>
            creating ? (
              <SimpleSelect
                id={id}
                value={form.collection}
                onChange={(collection) => patch({ collection })}
                options={[{ value: "", label: "Choose a collection", disabled: true }, ...collections.map((c) => ({ value: c.key, label: `${c.name} (${c.scope})` }))]}
              />
            ) : (
              <Input id={id} value={collections.find((c) => c.key === form.collection)?.name ?? form.collection} disabled />
            )
          }
        </Field>
        {collectionsError ? <ErrorAlert error={collectionsError} /> : null}

        <div className="flex flex-col gap-4 rounded-lg border bg-muted/20 p-3">
          {form.kind === "web" ? (
            <>
              {lines("web.startUrls", "Start URLs", form.web.startUrls, (startUrls) => patch({ web: { ...form.web, startUrls } }), {
                hint: "One per line. The crawler follows links on the same site and respects robots.txt.",
                placeholder: "https://intranet.example/handbook/",
              })}
              <div className="grid gap-4 sm:grid-cols-2">
                {text("web.maxPages", "Max pages", form.web.maxPages, (maxPages) => patch({ web: { ...form.web, maxPages } }), { hint: "1–5000. Deletions only apply when the crawl finished within this." })}
                {text("web.pathPrefix", "Path prefix (optional)", form.web.pathPrefix, (pathPrefix) => patch({ web: { ...form.web, pathPrefix } }), { placeholder: "/handbook/", mono: true })}
              </div>
            </>
          ) : form.kind === "github" ? (
            <>
              <div className="grid gap-4 sm:grid-cols-2">
                {text("github.repo", "Repository", form.github.repo, (repo) => patch({ github: { ...form.github, repo } }), { placeholder: "acme/handbook", mono: true })}
                {text("github.branch", "Branch (optional)", form.github.branch, (branch) => patch({ github: { ...form.github, branch } }), { placeholder: "default branch", mono: true })}
              </div>
              {lines("github.paths", "Paths (optional)", form.github.paths, (paths) => patch({ github: { ...form.github, paths } }), { hint: "One folder per line; empty syncs the whole repository.", placeholder: "docs/" })}
            </>
          ) : form.kind === "slack" ? (
            <>
              {lines("slack.channels", "Channel ids", form.slack.channels, (channels) => patch({ slack: { ...form.slack, channels } }), {
                hint: "One per line, like C0123ABCD (channel details → About). Invite the bot to each channel.",
                placeholder: "C0123ABCD",
              })}
              {text("slack.days", "Days of history", form.slack.days, (days) => patch({ slack: { ...form.slack, days } }), { hint: "1–3650." })}
            </>
          ) : (
            text("gdrive.folder", "Folder", form.gdrive.folder, (folder) => patch({ gdrive: { folder } }), {
              hint: "Paste the folder's link or id; subfolders are included. Connect your Google account after saving.",
              placeholder: "https://drive.google.com/drive/folders/…",
            })
          )}
          {server.config ? <p className="text-xs text-destructive">{server.config}</p> : null}
        </div>

        {creating && credentialFields(form.kind).length > 0 ? (
          <fieldset className="flex flex-col gap-3">
            <legend className="mb-1 text-sm font-medium">Credentials</legend>
            <p className="-mt-1 text-xs text-muted-foreground">Stored encrypted and never shown again. You can replace them later.</p>
            {credentialFields(form.kind).map((field) => (
              <Field key={field} label={`${CREDENTIAL_LABELS[field]?.label ?? field}${CREDENTIAL_LABELS[field]?.optional ? " (optional)" : ""}`} hint={CREDENTIAL_LABELS[field]?.hint} error={shown(`credentials.${field}`)}>
                {({ id, describedBy, invalid }) => (
                  <Input id={id} type="password" autoComplete="off" value={form.credentials[field] ?? ""} onChange={(e) => patch({ credentials: { ...form.credentials, [field]: e.target.value } })} aria-describedby={describedBy} aria-invalid={invalid || undefined} />
                )}
              </Field>
            ))}
          </fieldset>
        ) : null}

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Sync" error={shown("intervalMinutes")}>
            {({ id }) => (
              <SimpleSelect
                id={id}
                value={String(form.intervalMinutes)}
                onChange={(v) => patch({ intervalMinutes: Number(v) })}
                options={[
                  ...INTERVALS.map((i) => ({ value: String(i.value), label: i.label })),
                  ...(INTERVALS.some((i) => i.value === form.intervalMinutes) ? [] : [{ value: String(form.intervalMinutes), label: intervalLabel(form.intervalMinutes) }]),
                ]}
              />
            )}
          </Field>
          <div className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2">
            <div>
              <p className="text-sm font-medium">Enabled</p>
              <p className="text-xs text-muted-foreground">Paused sources keep their documents.</p>
            </div>
            <Switch checked={form.enabled} onCheckedChange={(enabled) => patch({ enabled })} aria-label="Enabled" />
          </div>
        </div>
      </div>

      {error && !handled ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={() => onDone(false)} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? "Saving…" : creating ? "Create and sync" : "Save source"}
        </Button>
      </DialogFooter>
    </form>
  );
}

function CredentialsForm({ source, onDone }: { source: Source; onDone: () => void }) {
  const fields = source.needs.filter((f) => f !== "refresh_token");
  const [values, setValues] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function save(clear: boolean) {
    setPending(true);
    setError(null);
    try {
      await sourcesApi.setCredentials(source.key, clear ? {} : (credentialsPayload(values) ?? {}));
      await invalidateQueries(sourcesKeys.all);
      toast({ title: clear ? "Credentials removed" : "Credentials saved", description: source.name });
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
        void save(false);
      }}
    >
      <DialogHeader>
        <DialogTitle>Credentials · {source.name}</DialogTitle>
        <DialogDescription>
          {source.has_credentials ? "Credentials are stored. They're never shown; enter new ones to replace them." : "Nothing stored yet."}
        </DialogDescription>
      </DialogHeader>
      <div className="flex flex-col gap-3">
        {fields.map((field) => (
          <Field key={field} label={CREDENTIAL_LABELS[field]?.label ?? field} hint={CREDENTIAL_LABELS[field]?.hint}>
            {({ id, describedBy }) => (
              <Input id={id} type="password" autoComplete="off" value={values[field] ?? ""} onChange={(e) => setValues((v) => ({ ...v, [field]: e.target.value }))} aria-describedby={describedBy} />
            )}
          </Field>
        ))}
      </div>
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        {source.has_credentials ? (
          <Button type="button" variant="ghost" className="mr-auto text-destructive" disabled={pending} onClick={() => void save(true)}>
            Remove credentials
          </Button>
        ) : null}
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending || !credentialsPayload(values)}>
          {pending ? "Saving…" : "Save credentials"}
        </Button>
      </DialogFooter>
    </form>
  );
}

const STALE_WINDOWS = [
  { value: "90", label: "Not updated in 3 months" },
  { value: "180", label: "Not updated in 6 months" },
  { value: "365", label: "Not updated in a year" },
  { value: "730", label: "Not updated in 2 years" },
];

/** Documents past `valid_until` or not updated for a while: titles only, never content. */
function StaleDocuments() {
  const [days, setDays] = useState("365");
  const stale = useApiQuery(sourcesKeys.stale(Number(days)), () => sourcesApi.stale(Number(days)));
  const items = stale.state.status === "success" ? stale.state.data : [];
  return (
    <section className="flex flex-col gap-3 rounded-xl border p-4" aria-labelledby="stale-heading">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 id="stale-heading" className="font-medium">
            Needs review{stale.state.status === "success" ? <span className="ml-1 text-sm font-normal text-muted-foreground">({items.length})</span> : null}
          </h4>
          <p className="text-xs text-muted-foreground">Expired documents, or ones nobody updated for a while. Old answers come from old documents.</p>
        </div>
        <SimpleSelect size="sm" ariaLabel="How old" value={days} onChange={setDays} options={STALE_WINDOWS} />
      </div>
      {stale.state.status === "loading" ? (
        <TableSkeleton rows={2} />
      ) : stale.state.status === "error" ? (
        <ErrorAlert error={stale.state.error} onRetry={() => void stale.refetch()} />
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing to review.</p>
      ) : (
        <ul className="flex max-h-72 flex-col divide-y overflow-y-auto">
          {items.slice(0, 50).map((doc) => (
            <li key={doc.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
              {doc.title === null ? (
                <span className="min-w-0 truncate text-muted-foreground italic">Title withheld: you can&apos;t open this document</span>
              ) : (
                <Link href={`/knowledge/documents/${doc.id}`} className="min-w-0 truncate underline-offset-2 hover:underline">
                  {doc.title}
                </Link>
              )}
              <span className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className="font-mono">{doc.collection}</span>
                {doc.reason === "expired" ? (
                  <Badge variant="secondary" className="bg-destructive/10 text-destructive">
                    Expired {formatDate(doc.valid_until)}
                  </Badge>
                ) : (
                  <Badge variant="secondary">Last updated {formatDate(doc.updated_at)}</Badge>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
