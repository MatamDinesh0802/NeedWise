"use client";

import { CircleAlert, CircleCheck, CircleHelp, DatabaseZap, KeyRound, ListChecks, LoaderCircle, Plug, Plus, Power, Save, Trash2, Wand2 } from "lucide-react";
import { useId, useState, type KeyboardEvent } from "react";

import { ConfirmDialog } from "@/components/data/confirm-dialog";
import { Field, SimpleSelect } from "@/components/data/form";
import { EmptyState, ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { modelProblems } from "@/lib/analytics/admin";
import { analyticsApi, analyticsKeys, type CatalogTable, type SourceCatalog, type SourceKind, type SqlSource, type TlsMode } from "@/lib/analytics/api";
import {
  DEFAULT_PORTS,
  emptySourceForm,
  KIND_LABELS,
  scaffoldSourceModel,
  sourceFormErrors,
  sourceStatus,
  suggestSourceKey,
  TLS_LABELS,
  toSourceInput,
  type SourceForm,
} from "@/lib/analytics/sources";
import { formatDateTime } from "@/lib/format";
import { toast } from "@/lib/stores/toasts";
import { cn } from "@/lib/utils";

/**
 * Admin › Analytics › Databases: PostgreSQL and MySQL sources, each a dataset of its own with its
 * own data model (docs/analytics-sql-sources.md). Connect one, test it, pick tables from what its
 * user can read, and save the model.
 */
export function SqlSourcesPanel() {
  const sources = useApiQuery(analyticsKeys.sources, analyticsApi.sources);
  const [adding, setAdding] = useState(false);

  return (
    <section aria-labelledby="sources-heading" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 id="sources-heading" className="flex items-center gap-2 font-medium">
          <DatabaseZap className="size-4 text-muted-foreground" aria-hidden="true" />
          Databases
          <RefreshingHint active={sources.state.status === "success" && sources.state.refreshing} />
        </h4>
        {!adding ? (
          <Button variant="outline" size="sm" onClick={() => setAdding(true)}>
            <Plus data-icon="inline-start" />
            Connect a database
          </Button>
        ) : null}
      </div>
      <p className="max-w-3xl text-sm text-muted-foreground">
        A PostgreSQL or MySQL database is queried live, read-only, as its own dataset with its own data model and access. Give NeedWise a
        database user that may only read (<code>SELECT</code>) the tables people may ask about.
      </p>

      {adding ? <AddSourceForm onDone={() => setAdding(false)} /> : null}

      {sources.state.status === "loading" ? (
        <TableSkeleton rows={2} />
      ) : sources.state.status === "error" ? (
        <ErrorAlert error={sources.state.error} onRetry={() => void sources.refetch()} />
      ) : sources.state.data.length === 0 ? (
        adding ? null : (
          <EmptyState icon={DatabaseZap} title="No databases connected" description="Insights uses the uploaded tables only." />
        )
      ) : (
        <ul className="flex flex-col gap-4">
          {sources.state.data.map((source) => (
            <SourceCard key={source.key} source={source} />
          ))}
        </ul>
      )}
    </section>
  );
}

// --- Connecting --------------------------------------------------------------------------------

const KIND_OPTIONS = (["postgres", "mysql"] as const).map((value) => ({ value, label: KIND_LABELS[value] }));
const TLS_OPTIONS = (["verify-full", "require", "disable"] as const).map((value) => ({ value, label: TLS_LABELS[value] }));

export function AddSourceForm({ onDone }: { onDone: () => void }) {
  const [form, setForm] = useState<SourceForm>(emptySourceForm());
  const [keyEdited, setKeyEdited] = useState(false);
  const [showErrors, setShowErrors] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const headingId = useId();
  const errors = sourceFormErrors(form);
  const shown = (field: keyof SourceForm) => (showErrors ? (errors[field] ?? null) : null);

  function set<K extends keyof SourceForm>(field: K, value: SourceForm[K]) {
    setForm((current) => {
      const next = { ...current, [field]: value };
      if (field === "name" && !keyEdited) next.key = suggestSourceKey(String(value));
      if (field === "kind" && current.schemas === emptySourceForm(current.kind).schemas) next.schemas = emptySourceForm(value as SourceKind).schemas;
      return next;
    });
  }

  async function submit() {
    setShowErrors(true);
    if (Object.keys(errors).length > 0) return;
    setPending(true);
    setError(null);
    try {
      const created = await analyticsApi.createSource(toSourceInput(form));
      await invalidateQueries(analyticsKeys.all);
      toast({ title: `${created.name} connected`, description: "Test it, pick its tables and save a data model: until then nobody can ask about it." });
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  const text = (field: keyof SourceForm, label: string, options: { hint?: string; type?: string; mono?: boolean } = {}) => (
    <Field label={label} hint={options.hint} error={shown(field)}>
      {({ id, describedBy, invalid }) => (
        <Input
          id={id}
          type={options.type ?? "text"}
          className={cn(options.mono && "font-mono")}
          spellCheck={false}
          autoCapitalize="none"
          autoComplete={options.type === "password" ? "new-password" : "off"}
          value={String(form[field])}
          onChange={(e) => {
            if (field === "key") setKeyEdited(true);
            set(field, e.target.value as never);
          }}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
        />
      )}
    </Field>
  );

  return (
    <form
      noValidate
      aria-labelledby={headingId}
      className="flex flex-col gap-4 rounded-xl border border-dashed bg-muted/20 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <h5 id={headingId} className="flex items-center gap-2 text-sm font-medium">
        <Plug className="size-4 text-muted-foreground" aria-hidden="true" />
        Connect a database
      </h5>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Database type">
          {({ id }) => <SimpleSelect id={id} value={form.kind} onChange={(v) => set("kind", v)} options={KIND_OPTIONS} />}
        </Field>
        {text("name", "Name", { hint: "Shown to people, e.g. Sales warehouse." })}
        {text("key", "Key", { hint: "How the assistant names this dataset.", mono: true })}
        {text("host", "Host", { hint: "Host name or IP address. Private addresses must be allowed by the operator.", mono: true })}
        {text("port", "Port", { hint: `Empty: ${DEFAULT_PORTS[form.kind]}.`, mono: true })}
        {text("database", form.kind === "mysql" ? "Default database" : "Database", { mono: true })}
        {text("username", "User", { hint: "A user that may only read (SELECT).", mono: true })}
        {text("password", "Password", { type: "password", hint: "Stored encrypted; never shown again." })}
        {text("schemas", form.kind === "mysql" ? "Databases to read tables from" : "Schemas to read tables from", {
          hint: "Comma-separated. Tables outside them can't be used.",
          mono: true,
        })}
        <Field label="TLS">
          {({ id }) => <SimpleSelect<TlsMode> id={id} value={form.tls_mode} onChange={(v) => set("tls_mode", v)} options={TLS_OPTIONS} />}
        </Field>
      </div>
      {form.tls_mode === "verify-full" ? (
        <Field label="CA certificate (optional)" hint="PEM, when the server's certificate is signed by your own CA." error={shown("tls_ca")}>
          {({ id, describedBy, invalid }) => (
            <Textarea
              id={id}
              rows={3}
              className="font-mono text-xs"
              spellCheck={false}
              value={form.tls_ca}
              onChange={(e) => set("tls_ca", e.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid || undefined}
            />
          )}
        </Field>
      ) : null}
      {error ? <ErrorAlert error={error} /> : null}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <Plug data-icon="inline-start" />}
          {pending ? "Connecting…" : "Connect"}
        </Button>
      </div>
    </form>
  );
}

// --- One source ----------------------------------------------------------------------------

export function SourceCard({ source }: { source: SqlSource }) {
  const [testing, setTesting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const status = sourceStatus(source);
  const StatusIcon = status.tone === "ok" ? CircleCheck : status.tone === "error" ? CircleAlert : CircleHelp;

  async function test() {
    setTesting(true);
    setError(null);
    try {
      const check = await analyticsApi.testSource(source.key);
      await invalidateQueries(analyticsKeys.sources);
      toast(
        check.ok
          ? { title: `${source.name} answered`, description: `${check.server_version ?? "Connected"} · ${check.elapsed_ms} ms` }
          : { title: `${source.name} didn't answer`, description: check.error ?? undefined },
      );
    } catch (err) {
      setError(err);
    } finally {
      setTesting(false);
    }
  }

  async function toggle() {
    setError(null);
    try {
      await analyticsApi.updateSource(source.key, { enabled: !source.enabled });
      await invalidateQueries(analyticsKeys.sources);
    } catch (err) {
      setError(err);
    }
  }

  return (
    <li className="flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 font-medium">
            {source.name}
            <Badge variant="outline" className="font-mono font-normal">
              {source.key}
            </Badge>
            <Badge variant="secondary" className="font-normal">
              {KIND_LABELS[source.kind]}
            </Badge>
            {!source.enabled ? (
              <Badge variant="secondary" className="font-normal">
                Off
              </Badge>
            ) : null}
          </p>
          <p className="mt-0.5 font-mono text-xs break-all text-muted-foreground">
            {source.username}@{source.host}:{source.port}/{source.database} · {source.schemas.join(", ")} · TLS {source.tls_mode}
          </p>
          <p
            className={cn(
              "mt-1 flex items-start gap-1.5 text-xs",
              status.tone === "error" ? "text-destructive" : "text-muted-foreground",
            )}
          >
            <StatusIcon className={cn("mt-px size-3.5 shrink-0", status.tone === "ok" && "text-emerald-700 dark:text-emerald-400")} aria-hidden="true" />
            <span>
              {status.text}
              {source.checked_at ? <span className="text-muted-foreground"> · {formatDateTime(source.checked_at)}</span> : null}
            </span>
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Button variant="outline" size="sm" disabled={testing} onClick={() => void test()}>
            {testing ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <Plug data-icon="inline-start" />}
            Test connection
          </Button>
          <Button variant="ghost" size="sm" onClick={() => void toggle()} title={source.enabled ? "Insights stops using it" : "Insights uses it again"}>
            <Power data-icon="inline-start" />
            {source.enabled ? "Turn off" : "Turn on"}
          </Button>
          <Button variant="ghost" size="icon-sm" aria-label={`Remove ${source.name}`} title="Remove" onClick={() => setDeleting(true)}>
            <Trash2 />
          </Button>
        </div>
      </div>
      {error ? <ErrorAlert error={error} /> : null}
      <PasswordForm source={source} />
      <TablePicker source={source} onScaffold={(yaml) => setDraft(yaml)} />
      <SourceModelEditor key={`${source.yaml ?? ""}:${draft ?? ""}`} source={source} draft={draft} />
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={`Remove ${source.name}?`}
        description="Insights stops querying it and its data model is deleted. The database itself is not touched. Saved answers keep the rows they showed."
        confirmLabel="Remove database"
        onConfirm={async () => {
          await analyticsApi.deleteSource(source.key);
          await invalidateQueries(analyticsKeys.all);
          toast({ title: `${source.name} removed` });
        }}
      />
    </li>
  );
}

function PasswordForm({ source }: { source: SqlSource }) {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  if (!open) {
    return (
      <Button variant="link" size="xs" className="h-auto w-fit p-0" onClick={() => setOpen(true)}>
        <KeyRound data-icon="inline-start" />
        {source.password_set ? "Replace the password" : "Set the password"}
      </Button>
    );
  }
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!password) return;
        setPending(true);
        setError(null);
        try {
          await analyticsApi.updateSource(source.key, { password });
          await invalidateQueries(analyticsKeys.sources);
          toast({ title: "Password replaced" });
          setOpen(false);
          setPassword("");
        } catch (err) {
          setError(err);
        } finally {
          setPending(false);
        }
      }}
    >
      <Field label={`New password for ${source.username}`} className="min-w-56 flex-1">
        {({ id }) => <Input id={id} type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />}
      </Field>
      <Button type="submit" size="sm" disabled={pending || !password}>
        Save password
      </Button>
      <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>
        Cancel
      </Button>
      {error ? <ErrorAlert error={error} /> : null}
    </form>
  );
}

/** The tables the source's user can read, to pick from for a starting model. */
export function TablePicker({ source, onScaffold }: { source: SqlSource; onScaffold: (yaml: string) => void }) {
  const [catalog, setCatalog] = useState<SourceCatalog | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const keyOf = (t: CatalogTable) => `${t.schema}.${t.name}`;

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const result = await analyticsApi.sourceCatalog(source.key);
      setCatalog(result);
      void invalidateQueries(analyticsKeys.sources);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  if (!catalog) {
    return (
      <div className="flex flex-col gap-2">
        <Button variant="outline" size="sm" className="w-fit" disabled={loading} onClick={() => void load()}>
          {loading ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <ListChecks data-icon="inline-start" />}
          Show its tables
        </Button>
        {error ? <ErrorAlert error={error} /> : null}
      </div>
    );
  }
  return (
    <fieldset className="flex flex-col gap-2 rounded-lg border p-3">
      <legend className="px-1 text-sm font-medium">Tables {source.username} can read</legend>
      {catalog.tables.length === 0 ? (
        <p className="text-sm text-muted-foreground">None: grant the user SELECT on the tables, in the schemas listed above.</p>
      ) : (
        <ul className="grid gap-1.5 sm:grid-cols-2">
          {catalog.tables.map((table) => {
            const key = keyOf(table);
            const unusable = table.columns.filter((c) => !c.usable).length;
            return (
              <li key={key} className="flex items-start gap-2 text-sm">
                <Checkbox
                  id={`pick-${source.key}-${key}`}
                  checked={picked.has(key)}
                  onCheckedChange={(checked) =>
                    setPicked((current) => {
                      const next = new Set(current);
                      if (checked) next.add(key);
                      else next.delete(key);
                      return next;
                    })
                  }
                />
                <label htmlFor={`pick-${source.key}-${key}`} className="min-w-0">
                  <span className="font-mono text-xs break-all">{key}</span>
                  <span className="block text-xs text-muted-foreground">
                    {table.kind === "view" ? "view · " : ""}
                    {table.columns.length} columns{unusable > 0 ? ` (${unusable} not lower_snake_case: left out)` : ""}
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      )}
      {catalog.truncated ? <p className="text-xs text-amber-800 dark:text-amber-300">The list is cut: the schemas hold more columns than it shows.</p> : null}
      <div>
        <Button
          variant="outline"
          size="sm"
          disabled={picked.size === 0}
          onClick={() => onScaffold(scaffoldSourceModel(source, catalog.tables.filter((t) => picked.has(keyOf(t)))))}
        >
          <Wand2 data-icon="inline-start" />
          Write a starting model from {picked.size === 1 ? "1 table" : `${picked.size} tables`}
        </Button>
      </div>
    </fieldset>
  );
}

/** The source's data model, checked against the database when saved. */
export function SourceModelEditor({ source, draft }: { source: SqlSource; draft: string | null }) {
  const saved = source.yaml ?? "";
  const [text, setText] = useState(draft ?? saved);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const problemsId = useId();
  const dirty = text !== saved;
  const problems = modelProblems(error);

  async function save() {
    if (!dirty || pending) return;
    setPending(true);
    setError(null);
    try {
      await analyticsApi.saveSourceModel(source.key, text);
      await invalidateQueries(analyticsKeys.all);
      toast({ title: `Data model of ${source.name} saved`, description: "People in its access list can ask about it from the next question on." });
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      void save();
    }
  }

  return (
    <div className="flex min-w-0 flex-col gap-2">
      {problems.length > 0 ? (
        <Alert variant="destructive" id={problemsId}>
          <CircleAlert aria-hidden="true" />
          <AlertTitle>Not saved: {problems.length === 1 ? "1 problem" : `${problems.length} problems`}</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-4">
              {problems.map((problem, i) => (
                <li key={i} className="font-mono text-xs whitespace-pre-wrap">
                  {problem}
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : error ? (
        <ErrorAlert error={error} />
      ) : null}
      <Field
        label={`Data model of ${source.name} (YAML, ${KIND_LABELS[source.kind]} SQL)`}
        hint={
          <>
            Like the uploaded tables&apos; model, and each table names the table it reads: <code>source_table: schema.table</code>. Saving checks
            every table and column against the database. Ctrl/⌘+S saves.
          </>
        }
      >
        {({ id, describedBy }) => (
          <Textarea
            id={id}
            rows={14}
            spellCheck={false}
            autoCapitalize="none"
            autoCorrect="off"
            wrap="off"
            className="max-h-[30rem] min-h-48 min-w-0 overflow-auto font-mono text-xs leading-relaxed field-sizing-fixed"
            value={text}
            placeholder="Empty: show its tables, pick some and write a starting model, or paste one."
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            aria-describedby={[describedBy, problems.length > 0 ? problemsId : null].filter(Boolean).join(" ") || undefined}
            aria-invalid={problems.length > 0 || undefined}
          />
        )}
      </Field>
      <div className="flex justify-end">
        <Button size="sm" disabled={!dirty || pending} onClick={() => void save()}>
          {pending ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <Save data-icon="inline-start" />}
          {pending ? "Checking and saving…" : "Save model"}
        </Button>
      </div>
    </div>
  );
}
