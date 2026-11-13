"use client";

import { ArrowLeft, CalendarClock, Hand, ListTodo, UserRoundPen } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import { SignedInGate } from "@/components/auth/signed-in-gate";
import { SimpleSelect } from "@/components/data/form";
import { EmptyState, ErrorAlert, RefreshingHint } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ColleaguePicker } from "@/components/work/colleague-picker";
import { OriginBadge, useTaskNames, type Names } from "@/components/work/tasks-view";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { ApiError } from "@/lib/api";
import { errorMessage } from "@/lib/error-messages";
import { formatDateTime, formatRelative } from "@/lib/format";
import { canUseDirectory, personName, type Person } from "@/lib/people";
import type { Me } from "@/lib/session";
import { getQueryCache } from "@/lib/stores/query-cache";
import { toast } from "@/lib/stores/toasts";
import { cn } from "@/lib/utils";
import { workApi, workKeys, type Task, type TaskStatus, type TaskUpdate } from "@/lib/work/api";
import { assigneeText, canClaim, canSeeTask, dueText, TASK_CHANGE_KEYS, TASK_STATUS_LABELS, TASK_STATUSES, taskDocuments } from "@/lib/work/tasks";

/** The description; a stale-document reminder lists its documents as links instead of text. */
export function TaskBody({ task }: { task: Pick<Task, "description" | "origin"> }) {
  const { documents, more } = taskDocuments(task);
  if (documents.length === 0) {
    return task.description ? <p className="text-sm whitespace-pre-line">{task.description}</p> : null;
  }
  return (
    <div className="flex flex-col gap-2 text-sm">
      {task.description ? <p className="whitespace-pre-line">{task.description.split("\n\n")[0]}</p> : null}
      <ul className="flex flex-col gap-1" aria-label="Documents to check">
        {documents.map((doc) => (
          <li key={doc.id} className="flex flex-wrap items-center gap-2">
            <Link href={`/knowledge/documents/${encodeURIComponent(doc.id)}`} className="font-medium underline-offset-2 hover:underline">
              {doc.title}
            </Link>
            <span className="text-xs text-muted-foreground">
              {doc.reason === "missing_upstream" ? "no longer listed by the source" : "unchanged in the source for a long time"}
            </span>
          </li>
        ))}
      </ul>
      {more > 0 ? <p className="text-xs text-muted-foreground">…and {more} more, listed under Sources › Stale synced documents.</p> : null}
    </div>
  );
}

/** /tasks/[id] (notification and meeting links): one task, read with GET /tasks/{id}. */
export function TaskRoute() {
  const { id } = useParams<{ id: string }>();
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
      <Link href="/tasks" className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "w-fit")}>
        <ArrowLeft data-icon="inline-start" />
        Tasks
      </Link>
      <SignedInGate purpose="see this task">{(me) => <TaskDetail key={id} id={id} me={me} />}</SignedInGate>
    </div>
  );
}

function TaskDetail({ id, me }: { id: string; me: Me }) {
  const { state, refetch } = useApiQuery(workKeys.task(id), () => workApi.getTask(id));
  const { names } = useTaskNames(me);

  if (state.status === "loading") {
    return (
      <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading the task">
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }
  if (state.status === "error") {
    if (state.error instanceof ApiError && state.error.status === 404) {
      return (
        <EmptyState
          icon={ListTodo}
          title="Task not found"
          description="It doesn't exist, or it isn't yours to see: a task is visible to its assignee, its creator and the team whose queue it is in."
          action={
            <Link href="/tasks" className={buttonVariants({ variant: "outline", size: "sm" })}>
              Go to your tasks
            </Link>
          }
        />
      );
    }
    return <ErrorAlert error={state.error} onRetry={() => void refetch()} />;
  }
  return <TaskCard task={state.data} me={me} names={names} refreshing={state.refreshing} />;
}

function TaskCard({ task, me, names, refreshing }: { task: Task; me: Me; names: Names; refreshing: boolean }) {
  const router = useRouter();
  const [saving, setSaving] = useState(false);
  const [handingOver, setHandingOver] = useState(false);
  const due = dueText(task.due_at);
  const closed = task.status === "done" || task.status === "cancelled";
  const creator = personName(task.created_by, { meId: me.id, people: names.people, fallback: task.created_by ? "A colleague" : "The system" });

  async function update(body: TaskUpdate, done: string) {
    setSaving(true);
    try {
      const updated = await workApi.updateTask(task.id, body);
      toast({ title: done, description: updated.title });
      if (!canSeeTask(updated, me)) {
        // Handed to someone outside the viewer's reach: the task is theirs now.
        await invalidateQueries(workKeys.tasks);
        router.push("/tasks");
        return;
      }
      getQueryCache().setData<Task>(workKeys.task(task.id), () => updated);
      await Promise.all(TASK_CHANGE_KEYS.map(invalidateQueries));
      setHandingOver(false);
    } catch (error) {
      const message = errorMessage(error);
      toast({ title: message.title, description: message.description, tone: "error" });
    } finally {
      setSaving(false);
    }
  }

  function handOver(person: Person) {
    void update({ assignee_id: person.id }, `Handed over to ${person.display_name}`);
  }

  return (
    <article className="flex flex-col gap-4 rounded-xl border bg-card p-5" aria-labelledby="task-title">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 id="task-title" className={cn("text-xl font-semibold tracking-tight break-words", closed && "line-through decoration-muted-foreground/50")}>
            {task.title}
          </h2>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <Badge variant="secondary">{TASK_STATUS_LABELS[task.status]}</Badge>
            <OriginBadge task={task} />
            <RefreshingHint active={refreshing} />
          </div>
        </div>
      </header>

      <TaskBody task={task} />

      <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-2 text-sm">
        <dt className="text-muted-foreground">For</dt>
        <dd>{assigneeText(task, names)}</dd>
        <dt className="text-muted-foreground">Due</dt>
        <dd className={cn("flex items-center gap-1", due?.overdue && !closed && "text-destructive")}>
          {due ? (
            <>
              <CalendarClock className="size-3.5" aria-hidden="true" />
              {due.text}
            </>
          ) : (
            "No deadline"
          )}
        </dd>
        <dt className="text-muted-foreground">Created</dt>
        <dd>
          <span title={formatDateTime(task.created_at)}>{formatRelative(task.created_at)}</span> by {creator}
        </dd>
      </dl>

      <div className="flex flex-wrap items-center gap-2 border-t pt-4">
        <label className="text-sm text-muted-foreground" htmlFor={`status-${task.id}`}>
          Status
        </label>
        <SimpleSelect
          id={`status-${task.id}`}
          size="sm"
          value={task.status}
          disabled={saving}
          onChange={(status: TaskStatus) => void update({ status }, `Marked ${TASK_STATUS_LABELS[status].toLowerCase()}`)}
          options={TASK_STATUSES.map((s) => ({ value: s, label: TASK_STATUS_LABELS[s] }))}
        />
        {canClaim(task, me) ? (
          <Button variant="outline" size="sm" disabled={saving} onClick={() => void update({ assignee_id: me.id }, "Claimed: it's yours now")}>
            <Hand data-icon="inline-start" aria-hidden="true" />
            Claim
          </Button>
        ) : null}
        {canUseDirectory(me.org_role) && !closed ? (
          <Button variant="ghost" size="sm" disabled={saving} aria-expanded={handingOver} onClick={() => setHandingOver((open) => !open)}>
            <UserRoundPen data-icon="inline-start" aria-hidden="true" />
            Hand over
          </Button>
        ) : null}
      </div>

      {handingOver ? (
        <div className="flex flex-col gap-2 rounded-lg border bg-muted/20 p-3">
          <p className="text-sm font-medium">Hand it over to</p>
          <p className="text-xs text-muted-foreground">They get a notification. Unless you created it or share its team queue, you won&apos;t see the task afterwards.</p>
          <ColleaguePicker
            autoFocus
            disabled={saving}
            exclude={new Set(task.assignee_id ? [task.assignee_id] : [])}
            label="Find the colleague to hand the task to"
            onPick={handOver}
          />
        </div>
      ) : null}
    </article>
  );
}
