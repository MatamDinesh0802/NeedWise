"use client";

import {
  BookOpen,
  CircleAlert,
  CircleCheck,
  CirclePause,
  CirclePlay,
  Clock,
  FileCode,
  FolderTree,
  Globe,
  HardDrive,
  History,
  Info,
  KeyRound,
  LibraryBig,
  LoaderCircle,
  MessagesSquare,
  Plug,
  Plus,
  RefreshCw,
  ShieldCheck,
  Unplug,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { IdentityMappingsSection } from "@/components/admin/identity-mappings-section";
import { SourceDetail, StaleDocumentsPanel, type DetailTab } from "@/components/admin/source-detail";
import { Field, SimpleSelect } from "@/components/data/form";
import { EmptyState, ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { usePolling } from "@/hooks/use-polling";
import { useSession } from "@/hooks/use-session";
import {
  credentialsDropNote,
  accessSummary,
  afterCreate,
  anySyncing,
  configFieldKey,
  CREDENTIAL_LABELS,
  credentialFields,
  credentialsPayload,
  disconnectConfirmed,
  emptySourceForm,
  failureText,
  formFromSource,
  HEALTH_HINTS,
  HEALTH_LABELS,
  HEALTH_ORDER,
  healthCounts,
  intervalLabel,
  INTERVALS,
  KIND_HINTS,
  KIND_LABELS,
  nextSyncText,
  OAUTH_LABELS,
  OAUTH_ONLY,
  OAUTH_SETTINGS,
  PERMISSION_SYNC_SOURCES,
  oauthResult,
  SOURCE_KEY_MAX,
  sortSources,
  sourcesApi,
  sourcesKeys,
  staleAfterFromValue,
  staleAfterOptions,
  staleAfterValue,
  statsSummary,
  toCreatePayload,
  toUpdatePayload,
  validateSourceForm,
  whatSyncs,
  withKind,
  withSourceName,
  type OAuthBack,
  type Source,
  type SourceForm,
  type SourceFormErrors,
  type SourceHealth,
  type SourceKind,
  type SourceOptions,
  type SourcesScope,
} from "@/lib/admin/sources";
import { errorMessage, fieldErrors } from "@/lib/error-messages";
import { formatDateTime, formatRelative } from "@/lib/format";
import { toast } from "@/lib/stores/toasts";
import { cn } from "@/lib/utils";
import { detailErrors } from "@/lib/validation-detail";

const KIND_ICONS: Record<SourceKind, LucideIcon> = {
  web: Globe,
  github: FileCode,
  slack: MessagesSquare,
  gdrive: HardDrive,
  confluence: BookOpen,
  sharepoint: FolderTree,
};

const HEALTH_STYLE: Record<SourceHealth, string> = {
  healthy: "bg-emerald-500/15 text-emerald-800 dark:text-emerald-300",
  failing: "bg-red-500/10 text-red-800 dark:text-red-200",
  paused: "bg-muted text-muted-foreground",
  never_synced: "bg-amber-500/15 text-amber-900 dark:text-amber-200",
};

const HEALTH_ICONS: Record<SourceHealth, LucideIcon> = {
  healthy: CircleCheck,
  failing: CircleAlert,
  paused: CirclePause,
  never_synced: Clock,
};

/**
 * Sources and their health. `scope="all"`: Admin → Sources (org admins, every source).
 * `scope="owned"`: Knowledge Hub → Sources (the owners of the collections the sources sync into).
 * The API decides what each person sees and may do; the page only follows it. `embedded`: the
 * page has its own heading, so the section's is for screen readers only.
 */
export function SourcesSection({ scope = "all", embedded = false }: { scope?: SourcesScope; embedded?: boolean }) {
  const sources = useApiQuery(sourcesKeys.list, sourcesApi.list);
  const options = useApiQuery(sourcesKeys.options, sourcesApi.options);
  const [editing, setEditing] = useState<Source | "new" | null>(null);
  const [credentials, setCredentials] = useState<Source | null>(null);
  const [disconnecting, setDisconnecting] = useState<Source | null>(null);
  const [detail, setDetail] = useState<{ key: string; tab: DetailTab } | null>(null);
  const [filter, setFilter] = useState<SourceHealth | null>(null);
  const [watching, setWatching] = useState(false);

  const list = sources.state.status === "success" ? sources.state.data : [];
  const opts = options.state.status === "success" ? options.state.data : null;
  const back: OAuthBack = scope === "all" ? "admin" : "knowledge";
  usePolling(sourcesKeys.list, watching || anySyncing(list), 2500);
  // After "Sync now" the worker may take a moment to pick it up: keep looking for a minute.
  useEffect(() => {
    if (!watching) return;
    const timer = setTimeout(() => setWatching(false), 60_000);
    return () => clearTimeout(timer);
  }, [watching]);

  const shown = sortSources(filter ? list.filter((s) => s.health === filter) : list);
  const detailSource = detail ? (list.find((s) => s.key === detail.key) ?? null) : null;
  const canCreate = opts !== null && opts.collections.length > 0;
  // The API answers 403 to someone who manages no source (owns no collection).
  const notAManager = sources.state.status === "error" && errorMessage(sources.state.error).code === "forbidden";

  return (
    <section className="flex flex-col gap-4" aria-labelledby="sources-heading">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id="sources-heading" className={cn("flex items-center gap-2 text-lg font-semibold", embedded && "sr-only")}>
            Sources
          </h3>
          {embedded ? null : (
            <p className="text-sm text-muted-foreground">
              Keep a collection in sync with a website, a GitHub repository, Slack channels, a Drive folder, Confluence spaces or a SharePoint library.
            </p>
          )}
          <RefreshingHint active={sources.state.status === "success" && sources.state.refreshing} />
        </div>
        {notAManager ? null : (
          <Button onClick={() => setEditing("new")} disabled={!canCreate}>
            <Plus data-icon="inline-start" />
            New source
          </Button>
        )}
      </div>

      {notAManager ? null : (
        <Alert>
          <Info aria-hidden="true" />
          <AlertDescription>
            Everything synced from a source gets its collection&apos;s access rules: people see it only if they can see that collection.
            {opts ? ` A source that fails ${opts.failure_alert_after} syncs in a row notifies the people who manage it.` : ""}
          </AlertDescription>
        </Alert>
      )}

      <Suspense fallback={null}>
        <OAuthResultAlert />
      </Suspense>

      {sources.state.status === "loading" ? (
        <TableSkeleton rows={2} />
      ) : notAManager ? (
        <EmptyState
          icon={LibraryBig}
          title="You don't own a collection"
          description="Sources are managed by org admins and by the owners of the collections they sync into. Ask an admin to make you a collection's owner."
        />
      ) : sources.state.status === "error" ? (
        <ErrorAlert error={sources.state.error} onRetry={() => void sources.refetch()} />
      ) : list.length === 0 ? (
        <EmptyState
          icon={Globe}
          title="No sources yet"
          description={
            scope === "all"
              ? "Documents you upload stay as they are; a source keeps a collection up to date by itself."
              : "No source syncs into the collections you own. Add one to keep a collection up to date by itself."
          }
        />
      ) : (
        <>
          <HealthSummary sources={list} filter={filter} onFilter={setFilter} />
          {shown.length === 0 ? (
            <p className="text-sm text-muted-foreground">No source is {filter ? HEALTH_LABELS[filter].toLowerCase() : ""} right now.</p>
          ) : (
            <ul className="flex flex-col gap-3" aria-label="Sources">
              {shown.map((source) => (
                <SourceCard
                  key={source.id}
                  source={source}
                  scope={scope}
                  failureThreshold={opts?.failure_alert_after ?? null}
                  oauthConfigured={opts?.kinds.find((k) => k.kind === source.kind)?.oauth_configured ?? null}
                  back={back}
                  onEdit={() => setEditing(source)}
                  onCredentials={() => setCredentials(source)}
                  onDisconnect={() => setDisconnecting(source)}
                  onDetail={(tab) => setDetail({ key: source.key, tab })}
                  onSynced={() => setWatching(true)}
                />
              ))}
            </ul>
          )}
        </>
      )}

      {list.length > 0 ? <StaleDocumentsPanel /> : null}

      {/* Mapping source accounts to people and groups is for org admins (Admin → Sources). */}
      {scope === "all" && !notAManager ? <IdentityMappingsSection /> : null}

      <Dialog open={editing !== null} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl">
          {editing !== null ? (
            <SourceFormView
              key={editing === "new" ? "new" : editing.key}
              source={editing === "new" ? null : editing}
              options={opts}
              optionsError={options.state.status === "error" ? options.state.error : null}
              back={back}
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

      <Dialog open={disconnecting !== null} onOpenChange={(open) => !open && setDisconnecting(null)}>
        <DialogContent className="sm:max-w-md">
          {disconnecting ? <DisconnectForm key={disconnecting.key} source={disconnecting} onDone={() => setDisconnecting(null)} /> : null}
        </DialogContent>
      </Dialog>

      <Sheet open={detail !== null} onOpenChange={(open) => !open && setDetail(null)}>
        <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-xl">
          {detail && detailSource ? (
            <SourceDetail key={detail.key} source={detailSource} tab={detail.tab} onTab={(tab) => setDetail({ key: detail.key, tab })} />
          ) : null}
        </SheetContent>
      </Sheet>

      {/* ?source=… (notification links): open that source's history once the list has loaded. */}
      <Suspense fallback={null}>
        <OpenLinkedSource sources={sources.state.status === "success" ? list : null} onOpen={(key) => setDetail({ key, tab: "runs" })} />
      </Suspense>
    </section>
  );
}

function OpenLinkedSource({ sources, onOpen }: { sources: Source[] | null; onOpen: (key: string) => void }) {
  const params = useSearchParams();
  const handled = useRef<string | null>(null);
  useEffect(() => {
    const key = params.get("source");
    if (sources === null || !key || params.get("oauth") || handled.current === key) return;
    handled.current = key;
    if (sources.some((s) => s.key === key)) onOpen(key);
    else toast({ title: "Source not found", description: `There is no source “${key}” you manage.`, tone: "error" });
  }, [sources, params, onOpen]);
  return null;
}

/** Counts per health, each a filter toggle. */
export function HealthSummary({ sources, filter, onFilter }: { sources: readonly Source[]; filter: SourceHealth | null; onFilter: (health: SourceHealth | null) => void }) {
  const counts = healthCounts(sources);
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filter sources by health">
      <Button variant={filter === null ? "secondary" : "ghost"} size="sm" aria-pressed={filter === null} onClick={() => onFilter(null)}>
        All {sources.length}
      </Button>
      {HEALTH_ORDER.map((health) => {
        const Icon = HEALTH_ICONS[health];
        return (
          <Button
            key={health}
            variant={filter === health ? "secondary" : "ghost"}
            size="sm"
            aria-pressed={filter === health}
            disabled={counts[health] === 0 && filter !== health}
            onClick={() => onFilter(filter === health ? null : health)}
          >
            <Icon data-icon="inline-start" aria-hidden="true" className={health === "failing" && counts.failing > 0 ? "text-destructive" : undefined} />
            {HEALTH_LABELS[health]} {counts[health]}
          </Button>
        );
      })}
    </div>
  );
}

/** After a provider's consent screen the API redirects here with ?oauth=connected|failed. */
function OAuthResultAlert() {
  const result = oauthResult(useSearchParams());
  if (!result) return null;
  const name = OAUTH_LABELS[result.provider];
  if (result.ok) {
    return (
      <Alert className="border-emerald-500/40">
        <CircleCheck aria-hidden="true" />
        <AlertTitle>{name} connected</AlertTitle>
        <AlertDescription>{result.source ? `“${result.source}” can sync now.` : "The source can sync now."}</AlertDescription>
      </Alert>
    );
  }
  return (
    <Alert variant="destructive">
      <CircleAlert aria-hidden="true" />
      <AlertTitle>{name} wasn&apos;t connected</AlertTitle>
      <AlertDescription>The consent was cancelled or expired, or it came from another session. Try “Connect {name}” again.</AlertDescription>
    </Alert>
  );
}

async function startOAuth(source: Pick<Source, "key" | "oauth">, back: OAuthBack): Promise<void> {
  if (!source.oauth) return;
  const { url } = await sourcesApi.startOAuth(source.oauth, source.key, back);
  window.location.assign(url); // on to the consent screen
}

export function SourceCard({
  source,
  scope,
  failureThreshold,
  oauthConfigured,
  back,
  onEdit,
  onCredentials,
  onDisconnect,
  onDetail,
  onSynced,
  now,
}: {
  source: Source;
  scope: SourcesScope;
  failureThreshold: number | null;
  /** Whether "Connect" can work on this deployment (null: not known yet). */
  oauthConfigured: boolean | null;
  back: OAuthBack;
  onEdit: () => void;
  onCredentials: () => void;
  onDisconnect: () => void;
  onDetail: (tab: DetailTab) => void;
  onSynced: () => void;
  now?: Date;
}) {
  const [busy, setBusy] = useState<"sync" | "toggle" | "connect" | null>(null);
  const [connectError, setConnectError] = useState<unknown>(null);
  const Icon = KIND_ICONS[source.kind] ?? Globe;
  const HealthIcon = HEALTH_ICONS[source.health];
  const lastRun = source.last_run;
  const stats = lastRun ? statsSummary(lastRun.stats) : statsSummary(source.stats);
  const failures = failureText(source, failureThreshold);
  const staleTotal = source.stale.not_updated + source.stale.missing_upstream;
  const pastedCredentials = source.needs.filter((f) => f !== "refresh_token");
  const headingId = `source-${source.id}`;
  const unmappedCount = (lastRun ?? source).stats.acl_unmapped;
  const unmapped = typeof unmappedCount === "number" ? unmappedCount : 0;

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

  async function connect() {
    setBusy("connect");
    setConnectError(null);
    try {
      await startOAuth(source, back);
    } catch (error) {
      setConnectError(error);
      setBusy(null);
    }
  }

  return (
    <li className={cn("flex flex-col gap-3 rounded-xl border p-4", source.enabled ? "bg-card" : "border-dashed bg-muted/30")} aria-labelledby={headingId}>
      <div className="flex flex-wrap items-start gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border bg-muted/40">
          <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <h4 id={headingId} className="font-medium">
            {source.name} <span className="font-mono text-xs font-normal text-muted-foreground">{source.key}</span>
          </h4>
          <p className="text-sm text-muted-foreground">
            {KIND_LABELS[source.kind]} · {source.documents} {source.documents === 1 ? "document" : "documents"} · {intervalLabel(source.interval_minutes).toLowerCase()}
            {source.owner ? ` · set up by ${source.owner.display_name}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {source.syncing ? (
            <Badge variant="secondary" className="bg-sky-500/15 text-sky-800 dark:text-sky-300" role="status">
              <LoaderCircle className="animate-spin" data-icon="inline-start" aria-hidden="true" />
              Syncing…
            </Badge>
          ) : null}
          <Badge variant="secondary" className={HEALTH_STYLE[source.health]} title={HEALTH_HINTS[source.health]}>
            <HealthIcon data-icon="inline-start" aria-hidden="true" />
            {HEALTH_LABELS[source.health]}
          </Badge>
        </div>
      </div>

      <div className="rounded-lg border bg-muted/20 px-3 py-2 text-xs">
        <p className="font-medium text-foreground">Mapping</p>
        <p className="mt-0.5 break-words text-muted-foreground">
          <span className="text-foreground">{whatSyncs(source)}</span> →{" "}
          <Link href={`/knowledge/collections/${encodeURIComponent(source.collection_id)}`} className="text-foreground underline-offset-2 hover:underline">
            {source.collection_name || source.collection}
          </Link>{" "}
          → visible to {accessSummary(source.access)}
          {scope === "all" ? (
            <>
              {" "}
              (
              <Link href="/admin/collections" className="underline-offset-2 hover:underline">
                change who can see it
              </Link>
              )
            </>
          ) : null}
        </p>
        {source.permission_sync ? (
          <p className="mt-1 text-muted-foreground">
            <ShieldCheck className="mr-1 inline size-3 align-[-2px]" aria-hidden="true" />
            <span className="text-foreground">Permission sync:</span> each document is also limited to {PERMISSION_SYNC_SOURCES[source.kind] ?? "who can see it in the source"}.
            {unmapped > 0 ? ` ${unmapped} ${unmapped === 1 ? "account the source named maps" : "accounts the source named map"} to nobody in NeedWise yet${scope === "all" ? " (Source accounts, below)" : ""}.` : ""}
          </p>
        ) : null}
      </div>

      <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-4">
        <div>
          <dt className="text-muted-foreground">Last successful sync</dt>
          <dd title={formatDateTime(source.last_success_at)}>{source.last_success_at ? formatRelative(source.last_success_at, now) : "Never"}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Next scheduled sync</dt>
          <dd title={source.next_sync_at ? formatDateTime(source.next_sync_at) : undefined}>{nextSyncText(source, now)}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-muted-foreground">Last run</dt>
          <dd>{lastRun ? `${formatRelative(lastRun.started_at, now)}: ${lastRunText(lastRun, stats)}` : (stats ?? "No runs yet")}</dd>
        </div>
      </dl>

      {source.health === "failing" || source.last_error_plain ? (
        <div className="rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-800 dark:text-red-200" role="note" aria-label="Recent errors">
          <p className="font-medium">
            <CircleAlert className="mr-1 inline size-3.5 align-[-3px]" aria-hidden="true" />
            {source.last_error_plain ?? "The last sync failed."}
          </p>
          {failures ? <p className="mt-1">{failures}</p> : null}
          {source.last_error ? (
            <details className="mt-1">
              <summary className="cursor-pointer select-none">What the source said</summary>
              <p className="mt-1 font-mono break-words">{source.last_error}</p>
            </details>
          ) : null}
        </div>
      ) : null}
      {staleTotal > 0 ? (
        <p className="text-xs">
          <button type="button" className="underline underline-offset-2" onClick={() => onDetail("stale")}>
            {staleTotal} stale {staleTotal === 1 ? "document" : "documents"}
          </button>
          <span className="text-muted-foreground">
            {" "}
            ({source.stale.missing_upstream} missing upstream, {source.stale.not_updated} unchanged past the threshold)
          </span>
        </p>
      ) : null}
      {connectError ? <ConnectError error={connectError} provider={source.oauth} /> : null}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          <KeyRound className="mr-1 inline size-3 align-[-2px]" aria-hidden="true" />
          {credentialsText(source)}
        </p>
        <div className="flex flex-wrap gap-2">
          {source.oauth && (oauthConfigured !== false || OAUTH_ONLY.includes(source.kind)) ? (
            <Button variant="outline" size="sm" disabled={busy !== null} onClick={() => void connect()}>
              {busy === "connect" ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <Plug data-icon="inline-start" />}
              {source.has_credentials ? `Reconnect ${OAUTH_LABELS[source.oauth]}` : `Connect ${OAUTH_LABELS[source.oauth]}`}
            </Button>
          ) : null}
          {pastedCredentials.length > 0 ? (
            <Button variant="ghost" size="sm" onClick={onCredentials}>
              <KeyRound data-icon="inline-start" />
              Credentials
            </Button>
          ) : null}
          <Button variant="ghost" size="sm" onClick={() => onDetail("runs")}>
            <History data-icon="inline-start" />
            History
          </Button>
          <Button variant="ghost" size="sm" onClick={onDisconnect} aria-label={`Disconnect ${source.name}`}>
            <Unplug data-icon="inline-start" />
            Disconnect
          </Button>
          <Button variant="outline" size="sm" onClick={onEdit} aria-label={`Edit ${source.name}: what it syncs and its schedule`}>
            Edit
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={busy !== null}
            onClick={() =>
              void run(
                "toggle",
                () => (source.enabled ? sourcesApi.pause(source.key) : sourcesApi.resume(source.key)),
                source.enabled ? "Syncing paused" : "Syncing resumed",
              )
            }
          >
            {source.enabled ? <CirclePause data-icon="inline-start" /> : <CirclePlay data-icon="inline-start" />}
            {source.enabled ? "Pause" : "Resume"}
          </Button>
          <Button
            size="sm"
            disabled={busy !== null || source.syncing || !source.enabled}
            title={source.enabled ? undefined : "Resume the source to sync it"}
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
            {busy === "sync" || source.syncing ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <RefreshCw data-icon="inline-start" />}
            Sync now
          </Button>
        </div>
      </div>
    </li>
  );
}

function lastRunText(run: NonNullable<Source["last_run"]>, stats: string | null): string {
  if (run.status === "running") return "running";
  if (run.status === "error") return stats && stats !== "Nothing changed" ? `failed (${stats})` : "failed";
  return stats ?? "nothing changed";
}

function credentialsText(source: Source): string {
  const by = source.has_credentials && source.credentials_by ? ` · ${source.credentials_by.display_name}'s` : "";
  return baseCredentialsText(source) + by;
}

function baseCredentialsText(source: Source): string {
  if (source.kind === "gdrive") return source.has_credentials ? "Google account connected (tokens stored encrypted)" : "Not connected to Google yet";
  if (source.kind === "sharepoint") return source.has_credentials ? "Microsoft account connected (tokens stored encrypted)" : "Not connected to Microsoft yet";
  if (source.kind === "confluence") return source.has_credentials ? "Connected (tokens or API token stored encrypted, never shown)" : "Not connected to Confluence yet";
  if (source.kind === "slack") return source.has_credentials ? "Slack bot token stored (encrypted, never shown)" : "Not connected to Slack yet";
  if (source.kind === "github") return source.has_credentials ? "Token stored (encrypted, never shown)" : "No token (public repositories only)";
  return "No credentials needed";
}

function ConnectError({ error, provider }: { error: unknown; provider: Source["oauth"] }) {
  const code = errorMessage(error).code;
  if (code === "oauth_not_configured" && provider) {
    const env = OAUTH_SETTINGS[provider];
    return (
      <Alert>
        <Info aria-hidden="true" />
        <AlertTitle>{OAUTH_LABELS[provider]} sign-in isn&apos;t set up on this server</AlertTitle>
        <AlertDescription>
          An administrator of the deployment needs to create an OAuth client and set <code>{env[0]}</code> and <code>{env[1]}</code>, with the redirect
          URI <code>…/api/v1/oauth/{provider}/callback</code> (see docs/connectors.md).
          {provider === "slack" ? " Until then, paste a bot token under Credentials." : provider === "atlassian" ? " Until then, enter an account email and API token under Credentials." : ""}
        </AlertDescription>
      </Alert>
    );
  }
  return <ErrorAlert error={error} />;
}

function SourceFormView({
  source,
  options,
  optionsError,
  back,
  onDone,
}: {
  source: Source | null;
  options: SourceOptions | null;
  optionsError: unknown;
  back: OAuthBack;
  onDone: (created: boolean) => void;
}) {
  const creating = source === null;
  const { state: session } = useSession();
  const meId = session.kind === "signed_in" ? session.me.id : null;
  const dropNote = source ? credentialsDropNote(source, meId) : null;
  const [form, setForm] = useState<SourceForm>(() => (source ? formFromSource(source) : emptySourceForm()));
  const [showErrors, setShowErrors] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const kindOption = options?.kinds.find((k) => k.kind === form.kind) ?? null;
  const slackOAuth = form.kind === "slack" && kindOption?.oauth_configured === true;
  const atlassianOAuth = form.kind === "confluence" && kindOption?.oauth_configured === true;
  const errors = validateSourceForm(form, { creating, slackOAuth, atlassianOAuth });
  const connectsAfter =
    OAUTH_ONLY.includes(form.kind) ||
    (slackOAuth && !form.credentials.bot_token?.trim()) ||
    (atlassianOAuth && !form.credentials.email?.trim() && !form.credentials.api_token?.trim());
  const server: SourceFormErrors = Object.fromEntries(fieldErrors(error).map((f) => [f.field, f.message]));
  for (const e of detailErrors(error, ["invalid_config"])) {
    const key = configFieldKey(form.kind, e.field);
    server[key] = server[key] ? `${server[key]} ${e.message}` : e.message;
  }
  const code = errorMessage(error).code;
  if (code === "slug_taken") server.key = "A source with this key already exists.";
  if (code === "unknown_collection") server.collection = "That collection no longer exists, or you don't own it.";
  const shown = (field: string) => (showErrors ? (errors[field] ?? null) : null) ?? server[field] ?? null;
  const patch = (change: Partial<SourceForm>) => setForm((f) => ({ ...f, ...change }));
  const handled = Boolean(server.key || server.collection || server.config || Object.keys(server).some((k) => k.includes(".")));
  const collections = options?.collections ?? [];

  async function save() {
    setShowErrors(true);
    if (Object.keys(errors).length > 0) return;
    setPending(true);
    setError(null);
    try {
      const saved = creating ? await sourcesApi.create(toCreatePayload(form)) : await sourcesApi.update(form.key, toUpdatePayload(form));
      const next = creating ? afterCreate(saved) : "nothing";
      if (next === "sync") await sourcesApi.sync(saved.key).catch(() => undefined); // the first sync right away
      await invalidateQueries(sourcesKeys.all);
      toast({
        title: creating ? `${saved.name} created` : `${saved.name} saved`,
        description: next === "connect" ? `Now connect ${saved.oauth ? OAUTH_LABELS[saved.oauth] : "the account"}.` : next === "sync" ? "The first sync has started." : undefined,
      });
      onDone(creating);
      if (next === "connect") {
        try {
          await startOAuth(saved, back);
        } catch {
          // Shown on the card: "Connect" explains a server without the OAuth client.
        }
      }
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
        <DialogDescription>
          {creating
            ? "Everything synced from this source gets the collection's access rules."
            : "What it syncs, how often, and when its documents count as stale. The collection is fixed: to sync somewhere else, add a new source."}
        </DialogDescription>
      </DialogHeader>

      <div className="flex flex-col gap-4">
        {dropNote ? (
          <Alert>
            <Info aria-hidden="true" />
            <AlertTitle>Credentials stay with the person who connected them</AlertTitle>
            <AlertDescription>{dropNote}</AlertDescription>
          </Alert>
        ) : null}
        {creating ? (
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-sm font-medium">Kind</legend>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3" role="radiogroup" aria-label="Kind">
              {(Object.keys(KIND_LABELS) as SourceKind[]).map((kind) => {
                const KindIcon = KIND_ICONS[kind];
                const active = form.kind === kind;
                return (
                  <button
                    key={kind}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    onClick={() => setForm((f) => withKind(f, kind, options))}
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

        <Field
          label="Collection"
          hint={creating ? (options?.scope === "owned" ? "One of the collections you own. Synced documents get its access rules." : "Synced documents go here and get its access rules.") : "Can't be changed: add a new source to sync somewhere else."}
          error={creating ? shown("collection") : null}
        >
          {({ id }) =>
            creating ? (
              <SimpleSelect
                id={id}
                value={form.collection}
                onChange={(collection) => patch({ collection })}
                options={[{ value: "", label: options ? "Choose a collection" : "Loading collections…", disabled: true }, ...collections.map((c) => ({ value: c.key, label: `${c.name} (${c.scope})` }))]}
              />
            ) : (
              <Input id={id} value={source.collection_name || form.collection} disabled />
            )
          }
        </Field>
        {optionsError ? <ErrorAlert error={optionsError} /> : null}

        <div className="flex flex-col gap-4 rounded-lg border bg-muted/20 p-3">
          {form.kind === "web" ? (
            <>
              {lines("web.startUrls", "Start URLs", form.web.startUrls, (startUrls) => patch({ web: { ...form.web, startUrls } }), {
                hint: "One per line. The crawler follows links on the same site and respects robots.txt.",
                placeholder: "https://intranet.example/handbook/",
              })}
              <div className="grid gap-4 sm:grid-cols-2">
                {text("web.maxPages", "Max pages", form.web.maxPages, (maxPages) => patch({ web: { ...form.web, maxPages } }), {
                  hint: "1–5000. A crawl cut short by this deletes nothing: pages it didn't reach are flagged “missing upstream”.",
                })}
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
                hint: "One per line, like C0123ABCD (channel details → About). Invite the app to each channel.",
                placeholder: "C0123ABCD",
              })}
              {text("slack.days", "Days of history", form.slack.days, (days) => patch({ slack: { ...form.slack, days } }), { hint: "1–3650." })}
            </>
          ) : form.kind === "confluence" ? (
            <>
              {text("confluence.siteUrl", "Site", form.confluence.siteUrl, (siteUrl) => patch({ confluence: { ...form.confluence, siteUrl } }), {
                hint: "Your Confluence Cloud address.",
                placeholder: "https://acme.atlassian.net",
                mono: true,
              })}
              {lines("confluence.spaces", "Space keys", form.confluence.spaces, (spaces) => patch({ confluence: { ...form.confluence, spaces } }), {
                hint: "One per line (the key is in the space's URL, like ENG). All of them go into this collection.",
                placeholder: "ENG",
              })}
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium" id="confluence-attachments-label">
                    Attachments
                  </p>
                  <p className="text-xs text-muted-foreground">Also sync the PDF, Office and text files attached to pages.</p>
                </div>
                <Switch
                  checked={form.confluence.includeAttachments}
                  onCheckedChange={(includeAttachments) => patch({ confluence: { ...form.confluence, includeAttachments } })}
                  aria-labelledby="confluence-attachments-label"
                />
              </div>
            </>
          ) : form.kind === "sharepoint" ? (
            <>
              {text("sharepoint.siteUrl", "Site (optional)", form.sharepoint.siteUrl, (siteUrl) => patch({ sharepoint: { ...form.sharepoint, siteUrl } }), {
                hint: "Empty: the OneDrive of the account that connects.",
                placeholder: "https://contoso.sharepoint.com/sites/finance",
                mono: true,
              })}
              <div className="grid gap-4 sm:grid-cols-2">
                {text("sharepoint.library", "Library (optional)", form.sharepoint.library, (library) => patch({ sharepoint: { ...form.sharepoint, library } }), {
                  hint: "Its name or drive id; empty: the site's default library.",
                  placeholder: "Documents",
                })}
                {text("sharepoint.folder", "Folder (optional)", form.sharepoint.folder, (folder) => patch({ sharepoint: { ...form.sharepoint, folder } }), {
                  hint: "Only this folder and its subfolders.",
                  placeholder: "Policies/2026",
                  mono: true,
                })}
              </div>
              <p className="text-xs text-muted-foreground">You sign in with Microsoft right after saving.</p>
            </>
          ) : (
            text("gdrive.folder", "Folder", form.gdrive.folder, (folder) => patch({ gdrive: { folder } }), {
              hint: "Paste the folder's link or id; subfolders are included. You connect your Google account right after saving.",
              placeholder: "https://drive.google.com/drive/folders/…",
            })
          )}
          {server.config ? <p className="text-xs text-destructive">{server.config}</p> : null}
        </div>

        {creating && credentialFields(form.kind).length > 0 ? (
          <fieldset className="flex flex-col gap-3">
            <legend className="mb-1 text-sm font-medium">Credentials</legend>
            <p className="-mt-1 text-xs text-muted-foreground">
              Stored encrypted and never shown again. You can replace them later.
              {slackOAuth ? " Or leave the token empty and connect Slack right after saving." : ""}
              {atlassianOAuth ? " Or leave both empty and connect Confluence right after saving." : ""}
            </p>
            {credentialFields(form.kind).map((field) => (
              <Field
                key={field}
                label={`${CREDENTIAL_LABELS[field]?.label ?? field}${CREDENTIAL_LABELS[field]?.optional || (field === "bot_token" && slackOAuth) || (form.kind === "confluence" && atlassianOAuth) ? " (optional)" : ""}`}
                hint={CREDENTIAL_LABELS[field]?.hint}
                error={shown(`credentials.${field}`)}
              >
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
          <Field label="Stale after" hint="Its owner is reminded once when a document hasn't changed in the source for this long.">
            {({ id, describedBy }) => (
              <SimpleSelect
                id={id}
                describedBy={describedBy}
                value={staleAfterValue(form.staleAfterDays)}
                onChange={(v) => patch({ staleAfterDays: staleAfterFromValue(v) })}
                options={staleAfterOptions(form.staleAfterDays)}
              />
            )}
          </Field>
        </div>
        <div className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2">
          <div>
            <p className="text-sm font-medium" id="source-enabled-label">
              Enabled
            </p>
            <p className="text-xs text-muted-foreground">Paused sources keep their documents and stay searchable.</p>
          </div>
          <Switch checked={form.enabled} onCheckedChange={(enabled) => patch({ enabled })} aria-labelledby="source-enabled-label" />
        </div>
      </div>

      {error && !handled ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={() => onDone(false)} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? "Saving…" : creating ? (connectsAfter ? "Create and connect" : "Create and sync") : "Save source"}
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

/** Disconnect = delete the source and everything it synced, after typing its key. */
export function DisconnectForm({ source, onDone }: { source: Source; onDone: () => void }) {
  const [typed, setTyped] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const ok = disconnectConfirmed(typed, source);

  async function disconnect() {
    if (!ok) return;
    setPending(true);
    setError(null);
    try {
      await sourcesApi.remove(source.key);
      await invalidateQueries(sourcesKeys.all);
      toast({ title: `${source.name} disconnected`, description: "Its documents are gone from search too." });
      onDone();
    } catch (err) {
      setError(err);
      setPending(false);
    }
  }

  return (
    <form
      className="contents"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        void disconnect();
      }}
    >
      <DialogHeader>
        <DialogTitle>Disconnect {source.name}?</DialogTitle>
        <DialogDescription>
          This removes the source, its stored credentials and its run history, and deletes the {source.documents}{" "}
          {source.documents === 1 ? "document" : "documents"} it synced into {source.collection_name || source.collection}, from the knowledge base and
          from search. Nothing changes in the source itself. It can&apos;t be undone. To only stop syncing, pause it instead.
        </DialogDescription>
      </DialogHeader>
      <Field label={`Type “${source.key}” to confirm`}>
        {({ id }) => (
          <Input id={id} value={typed} autoFocus autoComplete="off" spellCheck={false} className="font-mono" onChange={(e) => setTyped(e.target.value)} disabled={pending} />
        )}
      </Field>
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" variant="destructive" disabled={!ok || pending}>
          {pending ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <Unplug data-icon="inline-start" />}
          Disconnect and delete documents
        </Button>
      </DialogFooter>
    </form>
  );
}
