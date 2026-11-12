"use client";

import { CalendarClock, ChevronRight, CircleAlert, FileText, History, Info, LoaderCircle, Play, Plus, ShieldCheck, Wand2, Workflow as WorkflowIcon } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { Field } from "@/components/data/form";
import { EmptyState, ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { usePolling } from "@/hooks/use-polling";
import {
  BLANK_DEFINITION,
  definitionText,
  isRunActive,
  linkedWorkflow,
  parseDefinition,
  RUN_STATUS_LABELS,
  runSteps,
  runTriggerText,
  STEP_STATUS_LABELS,
  triggerText,
  waitingApprovalId,
  WEEKLY_OPS_TEMPLATE,
  workflowProblems,
  workflowsApi,
  workflowsKeys,
  type RunStatus,
  type Workflow,
  type WorkflowRun,
} from "@/lib/admin/workflows";
import { errorMessage, fieldErrors } from "@/lib/error-messages";
import { formatDateTime, formatRelative } from "@/lib/format";
import { slugError, suggestSlug } from "@/lib/slug";
import { toast } from "@/lib/stores/toasts";
import { cn } from "@/lib/utils";

const RUN_STYLE: Record<RunStatus, string> = {
  queued: "bg-muted text-muted-foreground",
  running: "bg-sky-500/15 text-sky-800 dark:text-sky-300",
  waiting_approval: "bg-amber-500/15 text-amber-800 dark:text-amber-300",
  succeeded: "bg-emerald-500/15 text-emerald-800 dark:text-emerald-300",
  failed: "bg-destructive/10 text-destructive",
  cancelled: "bg-muted text-muted-foreground",
};

function RunStatusBadge({ status }: { status: RunStatus }) {
  return (
    <Badge variant="secondary" className={RUN_STYLE[status] ?? ""}>
      {status === "running" ? <LoaderCircle className="animate-spin" data-icon="inline-start" aria-hidden="true" /> : null}
      {RUN_STATUS_LABELS[status] ?? status}
    </Badge>
  );
}

type Editing = { workflow: Workflow | null; template: boolean };

export function WorkflowsSection() {
  const workflows = useApiQuery(workflowsKeys.list, workflowsApi.list);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [history, setHistory] = useState<Workflow | null>(null);
  // The run a link (a notification, an approval) asked to show, expanded.
  const [focusRun, setFocusRun] = useState<string | null>(null);
  const [starting, setStarting] = useState<string | null>(null);

  async function runNow(workflow: Workflow) {
    setStarting(workflow.key);
    try {
      await workflowsApi.run(workflow.key);
      await invalidateQueries(workflowsKeys.runs(workflow.key));
      toast({ title: `${workflow.name} started`, description: "Steps that change something will wait for approval." });
      setHistory(workflow);
    } catch (error) {
      const message = errorMessage(error);
      toast({ title: message.title, description: message.description, tone: "error" });
    } finally {
      setStarting(null);
    }
  }

  return (
    <section className="flex flex-col gap-4" aria-labelledby="workflows-heading">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id="workflows-heading" className="flex items-center gap-2 text-lg font-semibold">
            Workflows
            <RefreshingHint active={workflows.state.status === "success" && workflows.state.refreshing} />
          </h3>
          <p className="text-sm text-muted-foreground">Research and actions on a schedule or on demand. You decide which steps need a person.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => setEditing({ workflow: null, template: true })}>
            <Wand2 data-icon="inline-start" />
            From the weekly-ops example
          </Button>
          <Button onClick={() => setEditing({ workflow: null, template: false })}>
            <Plus data-icon="inline-start" />
            New workflow
          </Button>
        </div>
      </div>

      <Alert>
        <Info aria-hidden="true" />
        <AlertDescription>
          A workflow runs as the admin who saved it, with their current access. Research steps only read; a step that sends or
          changes something becomes an approval, and the run waits until the approver decides.
        </AlertDescription>
      </Alert>

      {workflows.state.status === "loading" ? (
        <TableSkeleton rows={2} />
      ) : workflows.state.status === "error" ? (
        <ErrorAlert error={workflows.state.error} onRetry={() => void workflows.refetch()} />
      ) : workflows.state.data.length === 0 ? (
        <EmptyState
          icon={WorkflowIcon}
          title="No workflows yet"
          description="Start from the weekly operations report: it researches last week and emails leadership once Olivia approves."
          action={
            <Button variant="outline" size="sm" onClick={() => setEditing({ workflow: null, template: true })}>
              <Wand2 data-icon="inline-start" />
              Use the example
            </Button>
          }
        />
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {workflows.state.data.map((workflow) => (
            <li key={workflow.id} className={cn("flex flex-col gap-3 rounded-xl border bg-card p-4", !workflow.enabled && "opacity-70")}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-medium">{workflow.name}</p>
                  <p className="font-mono text-xs text-muted-foreground">{workflow.key}</p>
                </div>
                <Badge variant={workflow.enabled ? "secondary" : "outline"}>{workflow.enabled ? "Enabled" : "Disabled"}</Badge>
              </div>
              {workflow.definition.description ? <p className="text-sm text-muted-foreground">{workflow.definition.description}</p> : null}
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <CalendarClock className="size-3.5" aria-hidden="true" />
                {triggerText(workflow.definition)}
              </p>
              <ol className="flex flex-wrap items-center gap-1 text-xs">
                {workflow.definition.steps.map((step, index) => (
                  <li key={step.id} className="flex items-center gap-1">
                    {index > 0 ? <ChevronRight className="size-3 text-muted-foreground" aria-hidden="true" /> : null}
                    <Badge variant="outline" className="font-normal">
                      {step.research != null ? <FileText data-icon="inline-start" aria-hidden="true" /> : <ShieldCheck data-icon="inline-start" aria-hidden="true" />}
                      {step.id}
                      <span className="text-muted-foreground">{step.research != null ? "research" : step.tool}</span>
                    </Badge>
                  </li>
                ))}
              </ol>
              <div className="mt-auto flex flex-wrap justify-end gap-2 pt-1">
                <Button variant="ghost" size="sm" onClick={() => setHistory(workflow)}>
                  <History data-icon="inline-start" />
                  Runs
                </Button>
                <Button variant="outline" size="sm" onClick={() => setEditing({ workflow, template: false })}>
                  Edit
                </Button>
                <Button size="sm" disabled={starting === workflow.key || !workflow.enabled} onClick={() => void runNow(workflow)}>
                  {starting === workflow.key ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <Play data-icon="inline-start" />}
                  Run now
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <Dialog open={editing !== null} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-3xl">
          {editing ? (
            <WorkflowEditor
              key={editing.workflow?.key ?? (editing.template ? "template" : "new")}
              workflow={editing.workflow}
              template={editing.template}
              onDone={() => setEditing(null)}
            />
          ) : null}
        </DialogContent>
      </Dialog>

      <Sheet
        open={history !== null}
        onOpenChange={(open) => {
          if (open) return;
          setHistory(null);
          setFocusRun(null);
        }}
      >
        <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-xl">
          {history ? (
            <RunHistory
              key={history.key}
              workflow={history}
              focusRun={focusRun}
              onRun={() => void runNow(history)}
              running={starting === history.key}
            />
          ) : null}
        </SheetContent>
      </Sheet>

      {/* ?workflow=…&run=… (search params are runtime data: behind Suspense). */}
      <Suspense fallback={null}>
        <OpenLinkedWorkflow
          workflows={workflows.state.status === "success" ? workflows.state.data : null}
          onOpen={(workflow, runId) => {
            setHistory(workflow);
            setFocusRun(runId);
          }}
        />
      </Suspense>
    </section>
  );
}

/** Opens the runs of the workflow a link names, once per link, when the list has loaded. */
function OpenLinkedWorkflow({
  workflows,
  onOpen,
}: {
  workflows: Workflow[] | null;
  onOpen: (workflow: Workflow, runId: string | null) => void;
}) {
  const params = useSearchParams();
  const handled = useRef<string | null>(null);
  useEffect(() => {
    if (workflows === null) return;
    const linked = linkedWorkflow(workflows, params);
    const id = params.toString();
    if (!linked || handled.current === id) return;
    handled.current = id;
    if (linked.workflow) onOpen(linked.workflow, linked.runId);
    else toast({ title: "Workflow not found", description: `There is no workflow “${linked.key}” any more.`, tone: "error" });
  }, [workflows, params, onOpen]);
  return null;
}

function WorkflowEditor({ workflow, template, onDone }: { workflow: Workflow | null; template: boolean; onDone: () => void }) {
  const creating = workflow === null;
  const start = workflow ?? (template ? WEEKLY_OPS_TEMPLATE : { key: "", name: "", definition: BLANK_DEFINITION });
  const [name, setName] = useState<string>(start.name);
  const [key, setKey] = useState<string>(start.key);
  const [keyEdited, setKeyEdited] = useState(!creating || template);
  const [enabled, setEnabled] = useState(workflow?.enabled ?? true);
  const [text, setText] = useState(() => definitionText(start.definition));
  const [showErrors, setShowErrors] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const parsed = parseDefinition(text);
  const keyProblem = creating ? (key.length > 60 ? "At most 60 characters." : slugError(key)) : null;
  const nameProblem = name.trim() ? null : "Add a name.";
  const problems = workflowProblems(error, parsed.ok ? parsed.definition : null);
  const server = Object.fromEntries(fieldErrors(error).map((f) => [f.field, f.message]));
  const code = errorMessage(error).code;

  async function save() {
    setShowErrors(true);
    if (!parsed.ok || keyProblem || nameProblem) return;
    setPending(true);
    setError(null);
    try {
      const saved = await workflowsApi.save({ key, name: name.trim(), definition: parsed.definition, enabled });
      await invalidateQueries(workflowsKeys.all);
      toast({ title: creating ? `${saved.name} created` : `${saved.name} saved`, description: "It runs as you, with your current access." });
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
        void save();
      }}
    >
      <DialogHeader>
        <DialogTitle>{creating ? "New workflow" : `Edit ${workflow.name}`}</DialogTitle>
        <DialogDescription>Saved workflows run as you. Steps that change something always wait for approval.</DialogDescription>
      </DialogHeader>
      <div className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-[1fr_1fr_auto]">
          <Field label="Name" error={showErrors ? nameProblem : null}>
            {({ id, describedBy, invalid }) => (
              <Input
                id={id}
                maxLength={120}
                value={name}
                onChange={(e) => {
                  setName(e.target.value);
                  if (!keyEdited) setKey(suggestSlug(e.target.value).slice(0, 60).replace(/-+$/, ""));
                }}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
              />
            )}
          </Field>
          <Field label="Key" hint={creating ? "Can't be changed later." : "Can't be changed."} error={(showErrors || keyEdited) && creating ? keyProblem : (server.key ?? null)}>
            {({ id, describedBy, invalid }) => (
              <Input
                id={id}
                className="font-mono"
                spellCheck={false}
                autoCapitalize="none"
                maxLength={60}
                value={key}
                disabled={!creating}
                onChange={(e) => {
                  setKey(e.target.value);
                  setKeyEdited(true);
                }}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
              />
            )}
          </Field>
          <div className="flex flex-col gap-1.5">
            <span className="text-sm font-medium">Enabled</span>
            <div className="flex h-8 items-center">
              <Switch checked={enabled} onCheckedChange={setEnabled} aria-label="Enabled" />
            </div>
          </div>
        </div>

        <Field
          label="Definition (JSON)"
          hint={
            <>
              <code>trigger.schedule</code> is a cron expression in UTC; <code>{'{"event": "document.ready", "collection": "…"}'}</code>{" "}
              runs it when a document version is ready (in that collection, if given, and only for documents you can view); omit
              the trigger for manual only. Each step has an <code>id</code> and either <code>research</code> (a question) or{" "}
              <code>tool</code> with <code>arguments</code>; <code>approver</code> is an email. Templates:{" "}
              <code>{"{{ steps.gather.text }}"}</code>, <code>{"{{ today }}"}</code>, and for events <code>{"{{ event.title }}"}</code>,{" "}
              <code>{"{{ event.link }}"}</code>, <code>{"{{ event.collection }}"}</code>.
            </>
          }
          error={showErrors && !parsed.ok ? `${parsed.message}${parsed.line ? ` (line ${parsed.line}, column ${parsed.column})` : ""}` : null}
        >
          {({ id, describedBy, invalid }) => (
            <Textarea
              id={id}
              rows={18}
              spellCheck={false}
              className="font-mono text-xs leading-relaxed"
              value={text}
              onChange={(e) => setText(e.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid || undefined}
            />
          )}
        </Field>
        {creating && !template ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="-mt-2 w-fit"
            onClick={() => {
              setText(definitionText(WEEKLY_OPS_TEMPLATE.definition));
              if (!name.trim()) {
                setName(WEEKLY_OPS_TEMPLATE.name);
                if (!keyEdited) setKey(WEEKLY_OPS_TEMPLATE.key);
              }
            }}
          >
            <Wand2 data-icon="inline-start" />
            Insert the weekly-ops example
          </Button>
        ) : null}

        {problems.length > 0 ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertTitle>The workflow isn&apos;t valid yet</AlertTitle>
            <AlertDescription>
              <ul className="list-disc pl-4">
                {problems.map((p, i) => (
                  <li key={i}>
                    {p.where ? <span className="font-medium">{p.where}: </span> : null}
                    {p.message}
                  </li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        ) : null}
      </div>
      {error && problems.length === 0 && !server.key ? <ErrorAlert error={error} /> : null}
      {code === "invalid_workflow" && problems.length === 0 ? <p className="text-xs text-destructive">{errorMessage(error).description}</p> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? "Saving…" : creating ? "Create workflow" : "Save workflow"}
        </Button>
      </DialogFooter>
    </form>
  );
}

function RunHistory({
  workflow,
  focusRun,
  onRun,
  running,
}: {
  workflow: Workflow;
  focusRun: string | null;
  onRun: () => void;
  running: boolean;
}) {
  const runs = useApiQuery(workflowsKeys.runs(workflow.key), () => workflowsApi.runs(workflow.key));
  const data = runs.state.status === "success" ? runs.state.data : [];
  const active = data.some((r) => r.status === "running" || r.status === "queued");
  const waiting = data.some((r) => r.status === "waiting_approval");
  // Running: every 3 s. Waiting for a person: every 10 s (the decision may come from another device).
  usePolling(workflowsKeys.runs(workflow.key), active || waiting, active ? 3000 : 10_000);

  return (
    <div className="flex flex-col gap-4 p-4">
      <SheetHeader className="p-0">
        <SheetTitle>Runs · {workflow.name}</SheetTitle>
        <SheetDescription>The last 50 runs, newest first, with each step&apos;s output.</SheetDescription>
      </SheetHeader>
      <div className="flex items-center justify-between gap-2">
        <RefreshingHint active={runs.state.status === "success" && runs.state.refreshing} />
        <Button size="sm" className="ml-auto" disabled={running || !workflow.enabled} onClick={onRun}>
          {running ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <Play data-icon="inline-start" />}
          Run now
        </Button>
      </div>
      {runs.state.status === "loading" ? (
        <TableSkeleton rows={3} />
      ) : runs.state.status === "error" ? (
        <ErrorAlert error={runs.state.error} onRetry={() => void runs.refetch()} />
      ) : data.length === 0 ? (
        <EmptyState icon={History} title="No runs yet" description="Press Run now, or wait for the schedule." />
      ) : (
        <ol className="flex flex-col gap-3">
          {data.map((run, index) => (
            <RunItem
              key={run.id}
              run={run}
              workflow={workflow}
              defaultOpen={focusRun ? run.id === focusRun : index === 0}
              focused={run.id === focusRun}
            />
          ))}
        </ol>
      )}
    </div>
  );
}

function RunItem({
  run,
  workflow,
  defaultOpen,
  focused,
}: {
  run: WorkflowRun;
  workflow: Workflow;
  defaultOpen: boolean;
  focused: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen || isRunActive(run));
  const approvalId = waitingApprovalId(run);
  const steps = runSteps(run, workflow.definition);
  const item = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (focused) item.current?.scrollIntoView({ block: "nearest" });
  }, [focused]);
  return (
    <li ref={item} className={cn("rounded-xl border", focused && "ring-2 ring-ring/40")}>
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left hover:bg-muted/40"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <ChevronRight className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")} aria-hidden="true" />
        <span className="min-w-0 flex-1 text-sm">
          <span title={formatDateTime(run.created_at)}>{formatRelative(run.created_at)}</span>
          <span className="text-muted-foreground"> · {runTriggerText(run.trigger)}</span>
          {run.finished_at ? <span className="text-muted-foreground"> · finished {formatRelative(run.finished_at)}</span> : null}
        </span>
        <RunStatusBadge status={run.status} />
      </button>
      {open ? (
        <div className="flex flex-col gap-3 border-t px-3 py-3">
          {approvalId ? (
            <Alert className="border-amber-500/40">
              <ShieldCheck aria-hidden="true" />
              <AlertTitle>Paused for approval</AlertTitle>
              <AlertDescription>
                <p>Nothing has been sent. The run continues when the approver decides.</p>
                <Link href={`/approvals/${approvalId}`} className={cn(buttonVariants({ variant: "outline", size: "xs" }), "mt-1 w-fit")}>
                  Open the approval
                </Link>
              </AlertDescription>
            </Alert>
          ) : null}
          {run.error ? <p className="text-sm text-destructive">{run.error}</p> : null}
          <ol className="flex flex-col gap-2">
            {steps.map((step) => (
              <li key={step.id} className="rounded-lg bg-muted/30 px-3 py-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm">
                    <span className="font-mono font-medium">{step.id}</span>{" "}
                    <span className="text-muted-foreground">{step.kind === "research" ? "research" : step.what}</span>
                  </p>
                  <Badge variant="outline" className="font-normal">
                    {STEP_STATUS_LABELS[step.status] ?? (step.status === "pending" ? "Not reached" : step.status)}
                  </Badge>
                </div>
                {step.output?.text ? (
                  <pre className="mt-2 max-h-56 overflow-auto rounded-md bg-background p-2 font-sans text-xs leading-relaxed whitespace-pre-wrap">{step.output.text}</pre>
                ) : null}
                {step.output?.error ? <p className="mt-1 text-xs text-destructive">{step.output.error}</p> : null}
                {step.output?.approval_id && step.status !== "waiting_approval" ? (
                  <Link href={`/approvals/${step.output.approval_id}`} className="mt-1 inline-block text-xs underline underline-offset-2">
                    View the approval
                  </Link>
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </li>
  );
}
