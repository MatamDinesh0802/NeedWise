"use client";

import {
  Ban,
  BadgeCheck,
  CircleAlert,
  CircleCheck,
  Clock,
  ListTodo,
  LoaderCircle,
  Mail,
  PencilLine,
  ShieldCheck,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useId, useReducer } from "react";

import { Field } from "@/components/data/form";
import { ErrorAlert } from "@/components/data/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { AddressChips } from "@/components/work/address-chips";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { useColleagues } from "@/hooks/use-people";
import { formatDateTime, formatRelative } from "@/lib/format";
import { personName, type Person } from "@/lib/people";
import { getQueryCache } from "@/lib/stores/query-cache";
import { cn } from "@/lib/utils";
import { workApi, workKeys, type Approval } from "@/lib/work/api";
import {
  argumentRows,
  canDecide,
  decisionMeaning,
  decisionRequest,
  displayStatus,
  effectiveArguments,
  initialCardState,
  isEditable,
  latestApproval,
  OFFICIAL_KIND,
  originLabel,
  outcomeText,
  reduceCard,
  serverFieldErrors,
  STATUS_LABELS,
  subjectLink,
  toolTitle,
  withAssigneeName,
  type DisplayStatus,
  type Draft,
  type DraftErrors,
  type EmailDraft,
  type SubmitKind,
  type TaskDraft,
} from "@/lib/work/approvals";
import { getNotificationsStore } from "@/lib/work/notifications";

const TOOL_ICONS: Record<string, LucideIcon> = { send_email: Mail, create_task: ListTodo, [OFFICIAL_KIND]: BadgeCheck };

const STATUS_STYLE: Record<DisplayStatus, string> = {
  pending: "bg-amber-500/15 text-amber-800 dark:text-amber-300",
  running: "bg-sky-500/15 text-sky-800 dark:text-sky-300",
  executed: "bg-emerald-500/15 text-emerald-800 dark:text-emerald-300",
  failed: "bg-destructive/10 text-destructive",
  rejected: "bg-muted text-muted-foreground",
  expired: "bg-muted text-muted-foreground",
  superseded: "bg-muted text-muted-foreground",
};

export function ApprovalStatusBadge({ approval }: { approval: Approval }) {
  const status = displayStatus(approval);
  return (
    <Badge variant="secondary" className={cn("shrink-0", STATUS_STYLE[status])}>
      {status === "running" ? <LoaderCircle className="animate-spin" data-icon="inline-start" aria-hidden="true" /> : null}
      {STATUS_LABELS[status]}
    </Badge>
  );
}

/** Stores the decision everywhere the approval is shown (chat, inbox, page) and refreshes the bell. */
async function afterDecision(approval: Approval) {
  getQueryCache().setData<Approval>(workKeys.approval(approval.id), () => approval);
  await Promise.all([invalidateQueries(workKeys.approvals), getNotificationsStore().refresh()]);
}

/**
 * One proposed action and its decision. Nothing runs until the approver presses Approve
 * (or Edit & approve); Reject and expiry run nothing.
 */
export function ApprovalCard({
  approval: fetched,
  meId,
  compact = false,
}: {
  approval: Approval;
  meId: string | null;
  /** Inline in chat: tighter spacing, no origin line. */
  compact?: boolean;
}) {
  const [state, dispatch] = useReducer(reduceCard, initialCardState);
  const approval = latestApproval(fetched, state.decided);
  const status = displayStatus(approval);
  const decidable = canDecide(approval, meId);
  const Icon = TOOL_ICONS[approval.tool] ?? Wrench;
  const args = effectiveArguments(approval);
  // Names from the colleague directory (members; guests and loading states fall back to roles).
  const colleagues = useColleagues();
  const approver = personName(approval.approver_id, { meId, people: colleagues.byId, fallback: "the approver" });
  const baseRows = argumentRows(approval.tool, args);
  const rows = baseRows ? withAssigneeName(baseRows, colleagues.people) : null;
  const outcome = outcomeText(approval);
  const fieldErrors: DraftErrors = { ...serverFieldErrors(state.error), ...state.draftErrors };
  const meaning = decisionMeaning(approval.tool);
  const subject = subjectLink(approval);

  async function submit(kind: SubmitKind) {
    const request = decisionRequest(state, kind);
    if (!request.ok) {
      dispatch({ type: "invalid", errors: request.errors });
      return;
    }
    dispatch({ type: "submit", kind });
    try {
      const decided = await workApi.decide(approval.id, request.body);
      dispatch({ type: "decided", approval: decided });
      await afterDecision(decided);
    } catch (error) {
      dispatch({ type: "failed", error });
      // 409: someone decided first (or it expired); show what actually happened.
      await Promise.all([invalidateQueries(workKeys.approvals), getNotificationsStore().refresh()]);
    }
  }

  return (
    <article
      aria-label={`${toolTitle(approval.tool)}: ${STATUS_LABELS[status]}`}
      className={cn(
        "flex flex-col gap-3 rounded-xl border bg-card text-card-foreground",
        compact ? "p-3.5" : "p-5",
        status === "pending" && "border-amber-500/50 shadow-[0_0_0_3px] shadow-amber-500/10",
      )}
    >
      <header className="flex items-start gap-3">
        <div
          className={cn(
            "flex size-9 shrink-0 items-center justify-center rounded-lg border",
            status === "pending" ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300" : "bg-muted/50 text-muted-foreground",
          )}
        >
          <Icon className="size-4" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{toolTitle(approval.tool)}</p>
          <p className="text-sm font-medium break-words">{approval.summary}</p>
        </div>
        <ApprovalStatusBadge approval={approval} />
      </header>

      {status === "pending" ? (
        <div className="flex items-start gap-2 rounded-lg bg-amber-500/10 px-3 py-2 text-sm text-amber-900 dark:text-amber-200" role="note">
          <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <p>
            <span className="font-medium">Nothing has happened yet.</span>{" "}
            {meaning && decidable
              ? `${meaning.approve} ${meaning.reject}`
              : decidable
                ? "This only runs if you approve it. You can edit it first, or reject it."
                : `It only runs once ${approver} says yes.`}
          </p>
        </div>
      ) : null}

      {state.mode === "edit" && state.draft ? (
        <EditForm
          draft={state.draft}
          errors={fieldErrors}
          disabled={state.submitting !== null}
          onChange={(draft) => dispatch({ type: "draft", draft })}
          people={colleagues.people}
        />
      ) : rows ? (
        <ArgumentList rows={rows} edited={approval.edited_arguments !== null} />
      ) : (
        <div className="flex flex-col gap-1">
          <p className="text-xs font-medium text-muted-foreground">
            Arguments{approval.edited_arguments ? " (edited before approval)" : ""}
          </p>
          <pre className="max-h-64 overflow-auto rounded-lg bg-muted/50 p-3 font-mono text-xs">{JSON.stringify(args, null, 2)}</pre>
        </div>
      )}

      {fieldErrors.form ? <p className="text-xs text-destructive">{fieldErrors.form}</p> : null}
      {state.error?.kind === "invalid_input" && Object.keys(serverFieldErrors(state.error)).length === 0 ? (
        <p className="text-xs text-destructive">{state.error.message}</p>
      ) : null}

      {state.mode === "reject" ? (
        <Field label="Why not? (optional)" hint="The person who asked sees this note.">
          {({ id, describedBy }) => (
            <Textarea
              id={id}
              rows={2}
              maxLength={2000}
              value={state.note}
              autoFocus
              disabled={state.submitting !== null}
              onChange={(e) => dispatch({ type: "note", note: e.target.value })}
              aria-describedby={describedBy}
              placeholder="e.g. Not this week: the numbers aren't final yet"
            />
          )}
        </Field>
      ) : null}

      {subject ? (
        <Link href={subject} className="w-fit text-sm text-primary hover:underline">
          Open the document
        </Link>
      ) : null}

      {outcome ? <Outcome status={status} text={outcome} approval={approval} /> : null}

      {state.error?.kind === "not_pending" ? (
        <Alert>
          <CircleAlert aria-hidden="true" />
          <AlertTitle>Already decided</AlertTitle>
          <AlertDescription>{state.error.message}</AlertDescription>
        </Alert>
      ) : null}
      {state.error?.kind === "other" ? <ErrorAlert error={state.error.error} /> : null}

      {decidable ? <Actions state={state} onSubmit={(kind) => void submit(kind)} dispatch={dispatch} approval={approval} /> : null}

      <footer className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {!compact ? <span>{originLabel(approval)}</span> : null}
        <span title={formatDateTime(approval.created_at)}>Proposed {formatRelative(approval.created_at)}</span>
        {status === "pending" ? (
          <span className="flex items-center gap-1" title={formatDateTime(approval.expires_at)}>
            <Clock className="size-3" aria-hidden="true" />
            Expires {formatRelative(approval.expires_at)}
          </span>
        ) : approval.decided_at ? (
          <span title={formatDateTime(approval.decided_at)}>Decided {formatRelative(approval.decided_at)}</span>
        ) : null}
        {!decidable && status === "pending" && meId !== approval.approver_id ? <span>Waiting for {approver}</span> : null}
      </footer>
    </article>
  );
}

function Actions({
  state,
  approval,
  onSubmit,
  dispatch,
}: {
  state: ReturnType<typeof reduceCard>;
  approval: Approval;
  onSubmit: (kind: SubmitKind) => void;
  dispatch: (action: Parameters<typeof reduceCard>[1]) => void;
}) {
  const busy = state.submitting !== null;
  const spinner = (kind: SubmitKind) =>
    state.submitting === kind ? <LoaderCircle className="animate-spin" data-icon="inline-start" aria-hidden="true" /> : null;

  if (state.mode === "edit") {
    return (
      <div className="flex flex-wrap justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => dispatch({ type: "cancel" })}>
          Cancel
        </Button>
        <Button type="button" size="sm" disabled={busy} onClick={() => onSubmit("edit")}>
          {spinner("edit") ?? <CircleCheck data-icon="inline-start" aria-hidden="true" />}
          {state.submitting === "edit" ? "Approving…" : "Approve with my changes"}
        </Button>
      </div>
    );
  }
  if (state.mode === "reject") {
    return (
      <div className="flex flex-wrap justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => dispatch({ type: "cancel" })}>
          Cancel
        </Button>
        <Button type="button" variant="destructive" size="sm" disabled={busy} onClick={() => onSubmit("reject")}>
          {spinner("reject") ?? <Ban data-icon="inline-start" aria-hidden="true" />}
          {state.submitting === "reject" ? "Rejecting…" : "Reject, run nothing"}
        </Button>
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => dispatch({ type: "reject" })}>
        <Ban data-icon="inline-start" aria-hidden="true" />
        Reject
      </Button>
      {isEditable(approval.tool) ? (
        <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => dispatch({ type: "edit", approval })}>
          <PencilLine data-icon="inline-start" aria-hidden="true" />
          Edit & approve
        </Button>
      ) : null}
      <Button type="button" size="sm" disabled={busy} onClick={() => onSubmit("approve")}>
        {spinner("approve") ?? <CircleCheck data-icon="inline-start" aria-hidden="true" />}
        {state.submitting === "approve" ? "Approving…" : approveLabel(approval.tool)}
      </Button>
    </div>
  );
}

function approveLabel(tool: string): string {
  if (tool === "send_email") return "Approve and send";
  if (tool === OFFICIAL_KIND) return "Approve: make it official";
  return "Approve";
}

function ArgumentList({ rows, edited }: { rows: NonNullable<ReturnType<typeof argumentRows>>; edited: boolean }) {
  return (
    <div className="flex flex-col gap-2">
      {edited ? (
        <p className="flex items-center gap-1 text-xs text-muted-foreground">
          <PencilLine className="size-3" aria-hidden="true" />
          Edited by the approver before it ran
        </p>
      ) : null}
      <dl className="grid grid-cols-[5.5rem_1fr] gap-x-3 gap-y-2 text-sm">
        {rows.map((row) => (
          <div key={row.label} className="contents">
            <dt className="pt-0.5 text-xs font-medium text-muted-foreground">{row.label}</dt>
            <dd className="min-w-0">
              {Array.isArray(row.value) ? (
                <span className="flex flex-wrap gap-1">
                  {row.value.length > 0 ? (
                    row.value.map((v) => (
                      <Badge key={v} variant="secondary" className="font-normal">
                        {v}
                      </Badge>
                    ))
                  ) : (
                    <span className="text-muted-foreground">None</span>
                  )}
                </span>
              ) : row.multiline ? (
                <div className="max-h-72 overflow-y-auto rounded-lg border bg-muted/30 px-3 py-2 text-sm whitespace-pre-wrap">{row.value}</div>
              ) : (
                <span className="break-words">{row.value || <span className="text-muted-foreground">Empty</span>}</span>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function Outcome({ status, text, approval }: { status: DisplayStatus; text: string; approval: Approval }) {
  if (status === "executed") {
    return (
      <div className="flex items-start gap-2 rounded-lg bg-emerald-500/10 px-3 py-2 text-sm text-emerald-900 dark:text-emerald-200" role="status">
        <CircleCheck className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <p>
          <span className="font-medium">{approval.edited_arguments ? "Approved with changes. " : "Approved. "}</span>
          {text}
        </p>
      </div>
    );
  }
  if (status === "failed") {
    return (
      <Alert variant="destructive">
        <CircleAlert aria-hidden="true" />
        <AlertTitle>Approved, but it didn&apos;t run</AlertTitle>
        <AlertDescription>{text}</AlertDescription>
      </Alert>
    );
  }
  return (
    <div className="flex items-start gap-2 rounded-lg bg-muted/60 px-3 py-2 text-sm text-muted-foreground" role="status">
      <Ban className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <p>{text}</p>
    </div>
  );
}

function EditForm({
  draft,
  errors,
  disabled,
  onChange,
  people,
}: {
  draft: Draft;
  errors: DraftErrors;
  disabled: boolean;
  onChange: (draft: Draft) => void;
  people: readonly Person[];
}) {
  if (draft.kind === "email") return <EmailForm draft={draft} errors={errors} disabled={disabled} onChange={onChange} />;
  if (draft.kind === "task") return <TaskForm draft={draft} errors={errors} disabled={disabled} onChange={onChange} people={people} />;
  return (
    <Field label="Arguments (JSON)" hint="The tool checks these again before anything runs." error={errors.json ?? errors.form ?? null}>
      {({ id, describedBy, invalid }) => (
        <Textarea
          id={id}
          rows={10}
          spellCheck={false}
          className="font-mono text-xs"
          value={draft.text}
          disabled={disabled}
          onChange={(e) => onChange({ ...draft, text: e.target.value })}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
        />
      )}
    </Field>
  );
}

function EmailForm({
  draft,
  errors,
  disabled,
  onChange,
}: {
  draft: EmailDraft;
  errors: DraftErrors;
  disabled: boolean;
  onChange: (draft: EmailDraft) => void;
}) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border bg-muted/20 p-3">
      <Field label="To" error={errors.to ?? null} hint="Press Enter or comma after each address.">
        {({ id, describedBy, invalid }) => (
          <AddressChips
            id={id}
            values={draft.to}
            input={draft.toInput}
            disabled={disabled}
            describedBy={describedBy}
            invalid={invalid}
            onChange={(to, toInput) => onChange({ ...draft, to, toInput })}
          />
        )}
      </Field>
      <Field label="Cc" error={errors.cc ?? null}>
        {({ id, describedBy, invalid }) => (
          <AddressChips
            id={id}
            values={draft.cc}
            input={draft.ccInput}
            disabled={disabled}
            describedBy={describedBy}
            invalid={invalid}
            placeholder="Optional"
            onChange={(cc, ccInput) => onChange({ ...draft, cc, ccInput })}
          />
        )}
      </Field>
      <Field label="Subject" error={errors.subject ?? null}>
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            maxLength={200}
            value={draft.subject}
            disabled={disabled}
            onChange={(e) => onChange({ ...draft, subject: e.target.value })}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
          />
        )}
      </Field>
      <Field label="Body" error={errors.body ?? null}>
        {({ id, describedBy, invalid }) => (
          <Textarea
            id={id}
            rows={12}
            maxLength={50_000}
            value={draft.body}
            disabled={disabled}
            className="text-sm"
            onChange={(e) => onChange({ ...draft, body: e.target.value })}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
          />
        )}
      </Field>
    </div>
  );
}

function TaskForm({
  draft,
  errors,
  disabled,
  onChange,
  people,
}: {
  draft: TaskDraft;
  errors: DraftErrors;
  disabled: boolean;
  onChange: (draft: TaskDraft) => void;
  people: readonly Person[];
}) {
  // The assignee is an email address; the browser suggests colleagues as you type a name or address.
  const colleaguesList = useId();
  const input = (key: "title" | "assignee" | "department", label: string, hint?: string) => (
    <Field label={label} hint={hint} error={errors[key] ?? null}>
      {({ id, describedBy, invalid }) => (
        <Input
          id={id}
          value={draft[key]}
          disabled={disabled}
          list={key === "assignee" && people.length > 0 ? colleaguesList : undefined}
          autoComplete={key === "assignee" ? "off" : undefined}
          onChange={(e) => onChange({ ...draft, [key]: e.target.value })}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
        />
      )}
    </Field>
  );
  return (
    <div className="flex flex-col gap-3 rounded-lg border bg-muted/20 p-3">
      {input("title", "Title")}
      <Field label="Description" error={errors.description ?? null}>
        {({ id, describedBy }) => (
          <Textarea
            id={id}
            rows={4}
            maxLength={5000}
            value={draft.description}
            disabled={disabled}
            onChange={(e) => onChange({ ...draft, description: e.target.value })}
            aria-describedby={describedBy}
          />
        )}
      </Field>
      <div className="grid gap-3 sm:grid-cols-3">
        {input("assignee", "Assignee", people.length > 0 ? "A colleague's email: type a name to find it" : "Their email address")}
        {people.length > 0 ? (
          <datalist id={colleaguesList}>
            {people.map((p) => (
              <option key={p.id} value={p.email}>
                {p.display_name}
              </option>
            ))}
          </datalist>
        ) : null}
        {input("department", "Team queue", "Department key")}
        <Field label="Due" error={errors.due ?? null}>
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              type="date"
              value={draft.due}
              disabled={disabled}
              onChange={(e) => onChange({ ...draft, due: e.target.value })}
              aria-describedby={describedBy}
              aria-invalid={invalid || undefined}
            />
          )}
        </Field>
      </div>
    </div>
  );
}

/**
 * A card for an approval known by id (e.g. from an `approval.required` event). With `initial`
 * (e.g. from a list) it doesn't fetch; switching between the two keeps the card mounted, so an
 * edit in progress survives a list refresh.
 */
export function ApprovalById({
  id,
  initial,
  meId,
  compact,
  fallbackSummary,
}: {
  id: string;
  initial?: Approval;
  meId: string | null;
  compact?: boolean;
  fallbackSummary?: string;
}) {
  const { state, refetch } = useApiQuery(initial ? null : workKeys.approval(id), () => workApi.getApproval(id));
  const approval = initial ?? (state.status === "success" ? state.data : null);
  if (approval) return <ApprovalCard approval={approval} meId={meId} compact={compact} />;
  if (state.status === "error") return <ErrorAlert error={state.error} onRetry={() => void refetch()} />;
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-amber-500/40 p-3.5" aria-busy="true">
      <p className="text-sm font-medium">{fallbackSummary || "Loading the proposed action…"}</p>
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <ShieldCheck className="size-3.5" aria-hidden="true" />
        Waiting for approval. Nothing has happened yet.
      </p>
      <Skeleton className="h-16 w-full" />
    </div>
  );
}
