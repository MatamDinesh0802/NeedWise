"use client";

import { ChartColumnBig, CircleAlert, Database, FileSpreadsheet, Info, LoaderCircle, Save, Trash2, Undo2, Upload, Wand2 } from "lucide-react";
import Link from "next/link";
import { useId, useRef, useState, type KeyboardEvent } from "react";

import { ConfirmDialog } from "@/components/data/confirm-dialog";
import { Field } from "@/components/data/form";
import { EmptyState, ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { modelProblems, scaffoldModel, suggestTableName, tableNameError } from "@/lib/analytics/admin";
import { analyticsApi, analyticsKeys, tableContentType, type AdminAnalytics, type DatasetTable } from "@/lib/analytics/api";
import { formatCount } from "@/lib/analytics/format";
import { formatBytes } from "@/lib/format";
import { toast } from "@/lib/stores/toasts";
import { cn } from "@/lib/utils";

/** Admin → Analytics: the dataset Insights queries, and the semantic model (YAML) over it. */
export function AnalyticsSection() {
  const admin = useApiQuery(analyticsKeys.admin, analyticsApi.admin);

  return (
    <section className="flex flex-col gap-6" aria-labelledby="analytics-heading">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id="analytics-heading" className="flex items-center gap-2 text-lg font-semibold">
            Analytics
            <RefreshingHint active={admin.state.status === "success" && admin.state.refreshing} />
          </h3>
          <p className="max-w-3xl text-sm text-muted-foreground">
            The data people can ask about in Insights. Upload tables, then describe them in the data model: what each metric means,
            who may ask, and which rows each person sees.
          </p>
        </div>
        <Link href="/insights" className={buttonVariants({ variant: "outline" })}>
          <ChartColumnBig data-icon="inline-start" />
          Open Insights
        </Link>
      </div>

      <Alert>
        <Info aria-hidden="true" />
        <AlertDescription>
          Questions run as read-only SQL against these tables only, through the data model: the assistant can&apos;t reach other
          tables, and row policies are applied in code to every query, not left to the model.
        </AlertDescription>
      </Alert>

      {admin.state.status === "loading" ? (
        <TableSkeleton rows={3} />
      ) : admin.state.status === "error" ? (
        <ErrorAlert error={admin.state.error} onRetry={() => void admin.refetch()} />
      ) : (
        <>
          <DatasetPanel data={admin.state.data} />
          <ModelEditor key={admin.state.data.yaml ?? ""} yaml={admin.state.data.yaml} tables={admin.state.data.dataset.tables} />
        </>
      )}
    </section>
  );
}

// --- Dataset --------------------------------------------------------------------------------

export function DatasetPanel({ data }: { data: AdminAnalytics }) {
  const [deleting, setDeleting] = useState<DatasetTable | null>(null);
  const tables = data.dataset.tables;

  return (
    <section aria-labelledby="dataset-heading" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h4 id="dataset-heading" className="flex items-center gap-2 font-medium">
          <Database className="size-4 text-muted-foreground" aria-hidden="true" />
          Dataset
        </h4>
        {data.dataset.version ? (
          <Badge variant="outline" className="font-mono font-normal" title="Each upload or deletion makes a new version">
            version {data.dataset.version}
          </Badge>
        ) : null}
      </div>

      {tables.length === 0 ? (
        <EmptyState icon={FileSpreadsheet} title="No tables yet" description="Upload a CSV or Excel file below; each file becomes one table." />
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {tables.map((table) => (
            <li key={table.name} className="flex flex-col gap-2 rounded-xl border bg-card p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-mono text-sm font-medium break-all">{table.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {formatCount(table.rows)} {table.rows === 1 ? "row" : "rows"} · {table.columns.length}{" "}
                    {table.columns.length === 1 ? "column" : "columns"}
                  </p>
                </div>
                <Button variant="ghost" size="icon-sm" aria-label={`Delete table ${table.name}`} title="Delete table" onClick={() => setDeleting(table)}>
                  <Trash2 />
                </Button>
              </div>
              <ColumnList table={table} />
            </li>
          ))}
        </ul>
      )}

      <UploadTableForm tables={tables} />

      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={deleting ? `Delete table ${deleting.name}?` : "Delete table?"}
        description={
          deleting ? (
            <>
              This removes <span className="font-mono">{deleting.name}</span> ({formatCount(deleting.rows)} rows) in a new dataset version. If
              the data model uses it, questions that need it fail until you change the model.
            </>
          ) : null
        }
        confirmLabel="Delete table"
        onConfirm={async () => {
          if (!deleting) return;
          await analyticsApi.deleteTable(deleting.name);
          await invalidateQueries(analyticsKeys.all);
          toast({ title: `${deleting.name} deleted`, description: "Insights no longer sees it." });
        }}
      />
    </section>
  );
}

const SHOWN_COLUMNS = 8;

function ColumnList({ table }: { table: DatasetTable }) {
  const [all, setAll] = useState(false);
  const shown = all ? table.columns : table.columns.slice(0, SHOWN_COLUMNS);
  return (
    <div className="flex flex-col gap-1.5">
      <ul className="flex flex-wrap gap-1" aria-label={`Columns of ${table.name}`}>
        {shown.map((column) => (
          <li key={column.name} className="rounded-md border bg-muted/30 px-1.5 py-0.5 font-mono text-[11px]">
            {column.name} <span className="text-muted-foreground">{column.type.toLowerCase()}</span>
          </li>
        ))}
      </ul>
      {table.columns.length > SHOWN_COLUMNS ? (
        <Button variant="link" size="xs" className="h-auto w-fit p-0" aria-expanded={all} onClick={() => setAll((a) => !a)}>
          {all ? "Show fewer columns" : `Show all ${table.columns.length} columns`}
        </Button>
      ) : null}
    </div>
  );
}

function UploadTableForm({ tables }: { tables: readonly DatasetTable[] }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const fileId = useId();
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [nameEdited, setNameEdited] = useState(false);
  const [showErrors, setShowErrors] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const contentType = file ? tableContentType(file.name) : null;
  const fileProblem = !file ? "Choose a CSV or Excel (.xlsx) file." : !contentType ? "Only CSV and Excel (.xlsx) files can be uploaded." : null;
  const nameProblem = tableNameError(name);
  const replaces = tables.find((t) => t.name === name);

  function choose(next: File | null) {
    setFile(next);
    setError(null);
    if (next && !nameEdited) setName(suggestTableName(next.name));
  }

  async function upload() {
    setShowErrors(true);
    if (fileProblem || nameProblem || !file || !contentType) return;
    setPending(true);
    setError(null);
    try {
      const result = await analyticsApi.uploadTable(name, file, contentType);
      await invalidateQueries(analyticsKeys.all);
      const table = result.dataset.tables.find((t) => t.name === name);
      toast({
        title: `${name} ${replaces ? "replaced" : "uploaded"}`,
        description: table
          ? `${formatCount(table.rows)} rows, ${table.columns.length} columns${result.dataset.version ? ` · dataset version ${result.dataset.version}` : ""}.`
          : undefined,
      });
      setFile(null);
      setName("");
      setNameEdited(false);
      setShowErrors(false);
      if (fileRef.current) fileRef.current.value = "";
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      noValidate
      aria-labelledby={`${fileId}-heading`}
      className="flex flex-col gap-3 rounded-xl border border-dashed bg-muted/20 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        void upload();
      }}
    >
      <h5 id={`${fileId}-heading`} className="flex items-center gap-2 text-sm font-medium">
        <Upload className="size-4 text-muted-foreground" aria-hidden="true" />
        Upload a table
      </h5>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <label htmlFor={fileId} className="text-sm font-medium">
            File
          </label>
          <input
            id={fileId}
            ref={fileRef}
            type="file"
            accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            onChange={(e) => choose(e.target.files?.[0] ?? null)}
            aria-describedby={`${fileId}-hint${showErrors && fileProblem ? ` ${fileId}-error` : ""}`}
            aria-invalid={(showErrors && fileProblem !== null) || undefined}
            className={cn(
              "w-full rounded-lg border bg-background text-sm file:mr-3 file:border-0 file:border-r file:bg-muted file:px-3 file:py-1.5 file:text-sm file:font-medium",
              "focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
            )}
          />
          <p id={`${fileId}-hint`} className="text-xs text-muted-foreground">
            CSV with a header row, or the first sheet of an .xlsx file.{file ? ` ${file.name} · ${formatBytes(file.size)}` : ""}
          </p>
          {showErrors && fileProblem ? (
            <p id={`${fileId}-error`} className="text-xs text-destructive">
              {fileProblem}
            </p>
          ) : null}
        </div>
        <Field
          label="Table name"
          hint={replaces ? `Replaces the existing ${replaces.name} (${formatCount(replaces.rows)} rows).` : "lower_snake_case, e.g. invoice_lines."}
          error={showErrors || (nameEdited && name !== "") ? nameProblem : null}
        >
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              className="font-mono"
              spellCheck={false}
              autoCapitalize="none"
              maxLength={63}
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setNameEdited(true);
              }}
              aria-describedby={describedBy}
              aria-invalid={invalid || undefined}
            />
          )}
        </Field>
      </div>
      {error ? <ErrorAlert error={error} /> : null}
      <div className="flex justify-end">
        <Button type="submit" disabled={pending}>
          {pending ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <Upload data-icon="inline-start" />}
          {pending ? "Uploading…" : replaces ? "Replace table" : "Upload table"}
        </Button>
      </div>
    </form>
  );
}

// --- Semantic model -------------------------------------------------------------------------

export function ModelEditor({ yaml, tables }: { yaml: string | null; tables: readonly DatasetTable[] }) {
  const saved = yaml ?? "";
  const [text, setText] = useState(saved);
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
      await analyticsApi.saveModel(text);
      await invalidateQueries(analyticsKeys.all);
      toast({ title: "Data model saved", description: "Insights uses it from the next question on." });
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
    <section aria-labelledby="model-heading" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 id="model-heading" className="flex items-center gap-2 font-medium">
          Data model
          {dirty ? (
            <Badge variant="secondary" className="bg-amber-500/15 font-normal text-amber-800 dark:text-amber-300">
              Unsaved changes
            </Badge>
          ) : null}
        </h4>
        <div className="flex flex-wrap gap-2">
          {!text.trim() && tables.length > 0 ? (
            <Button variant="outline" size="sm" onClick={() => setText(scaffoldModel(tables))}>
              <Wand2 data-icon="inline-start" />
              Start from the uploaded tables
            </Button>
          ) : null}
          {dirty ? (
            <Button
              variant="ghost"
              size="sm"
              disabled={pending}
              onClick={() => {
                setText(saved);
                setError(null);
              }}
            >
              <Undo2 data-icon="inline-start" />
              Revert
            </Button>
          ) : null}
          <Button size="sm" disabled={!dirty || pending} onClick={() => void save()}>
            {pending ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <Save data-icon="inline-start" />}
            {pending ? "Saving…" : "Save model"}
          </Button>
        </div>
      </div>

      {yaml === null ? (
        <p className="text-sm text-muted-foreground">No data model yet: Insights stays off for everyone until you save one.</p>
      ) : null}

      {/* Above the editor, so a long model never pushes the problems out of sight. */}
      {problems.length > 0 ? (
        <Alert variant="destructive" id={problemsId}>
          <CircleAlert aria-hidden="true" />
          <AlertTitle>The data model wasn&apos;t saved: {problems.length === 1 ? "1 problem" : `${problems.length} problems`}</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-4">
              {problems.map((problem, i) => (
                <li key={i} className="font-mono text-xs">
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
        label="Semantic model (YAML)"
        hint={
          <>
            <code>access</code> lists who may ask (<code>org</code>, <code>department:finance</code>, <code>group:leadership</code>,{" "}
            <code>user:dan@acme.test</code>). <code>tables</code> and their columns, <code>joins</code>, <code>dimensions</code> and{" "}
            <code>metrics</code> are all the assistant sees. <code>row_policies</code> add a filter per table for the people they apply
            to, e.g. <code>{"account_owner = {user.email}"}</code>. Ctrl/⌘+S saves.
          </>
        }
      >
        {({ id, describedBy }) => (
          <Textarea
            id={id}
            rows={24}
            spellCheck={false}
            autoCapitalize="none"
            autoCorrect="off"
            wrap="off"
            className="max-h-[36rem] min-h-80 overflow-auto font-mono text-xs leading-relaxed"
            value={text}
            placeholder={tables.length > 0 ? "Empty. Start from the uploaded tables, or paste a model." : "Upload tables first, then describe them here."}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            aria-describedby={[describedBy, problems.length > 0 ? problemsId : null].filter(Boolean).join(" ") || undefined}
            aria-invalid={problems.length > 0 || undefined}
          />
        )}
      </Field>
    </section>
  );
}
