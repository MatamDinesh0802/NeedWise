"use client";

import { Bot, CalendarClock, CircleAlert, Hand, ListTodo, Plus, Workflow } from "lucide-react";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { PersonPicker } from "@/components/admin/person-picker";
import { SignedInGate } from "@/components/auth/signed-in-gate";
import { Field, SimpleSelect } from "@/components/data/form";
import { EmptyState, ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { Commitments } from "@/components/work/commitments";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { allUsersKey, fetchAllUsers } from "@/hooks/use-directory";
import { adminApi, adminKeys, type AdminUser } from "@/lib/admin/api";
import { errorMessage } from "@/lib/error-messages";
import { formatDateTime, formatRelative } from "@/lib/format";
import type { Me } from "@/lib/session";
import { toast } from "@/lib/stores/toasts";
import { cn } from "@/lib/utils";
import { workApi, workKeys, type Task, type TaskScope, type TaskStatus } from "@/lib/work/api";
import {
  assigneeText,
  dueText,
  emptyTaskForm,
  sortTasks,
  TASK_LIMITS,
  TASK_SCOPES,
  TASK_STATUS_LABELS,
  TASK_STATUSES,
  toTaskCreate,
  validateTaskForm,
  type AssignTo,
  type TaskForm,
} from "@/lib/work/tasks";

export function TasksView({ focusId }: { focusId?: string }) {
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6">
      <SignedInGate purpose="see your tasks">{(me) => <TaskBoard me={me} focusId={focusId ?? null} />}</SignedInGate>
    </div>
  );
}

/** /tasks/[id] (notification links): the board, opened on the tab that has the task, highlighted. */
export function TaskRoute() {
  const { id } = useParams<{ id: string }>();
  return <TasksView focusId={id} />;
}

interface Names {
  meId: string;
  people: ReadonlyMap<string, { display_name: string }>;
  departments: ReadonlyMap<string, { name: string }>;
}

function TaskBoard({ me, focusId }: { me: Me; focusId: string | null }) {
  const isAdmin = me.org_role === "owner" || me.org_role === "admin";
  const mine = useApiQuery(workKeys.taskList("mine"), () => workApi.listTasks("mine"));
  const queues = useApiQuery(workKeys.taskList("queues"), () => workApi.listTasks("queues"));
  const created = useApiQuery(workKeys.taskList("created"), () => workApi.listTasks("created"));
  // Names of other people and of every department need the admin directory.
  const users = useApiQuery(isAdmin ? allUsersKey : null, fetchAllUsers);
  const allDepartments = useApiQuery(isAdmin ? adminKeys.departments : null, adminApi.listDepartments);
  const [picked, setPicked] = useState<TaskScope | null>(null);
  const [creating, setCreating] = useState(false);

  const queries = { mine, queues, created };
  const lists: Record<TaskScope, Task[] | null> = {
    mine: mine.state.status === "success" ? mine.state.data : null,
    queues: queues.state.status === "success" ? queues.state.data : null,
    created: created.state.status === "success" ? created.state.data : null,
  };

  const departmentChoices = useMemo(
    () =>
      allDepartments.state.status === "success"
        ? allDepartments.state.data.items.map((d) => ({ id: d.id, slug: d.slug, name: d.name }))
        : me.departments.map((d) => ({ id: d.id, slug: d.slug, name: d.name })),
    [allDepartments.state, me.departments],
  );
  const names: Names = useMemo(
    () => ({
      meId: me.id,
      people: new Map((users.state.status === "success" ? users.state.data : []).map((u) => [u.id, { display_name: u.display_name }])),
      departments: new Map(departmentChoices.map((d) => [d.id, { name: d.name }])),
    }),
    [me.id, users.state, departmentChoices],
  );

  const focusScope = focusId ? TASK_SCOPES.find((s) => lists[s.value]?.some((t) => t.id === focusId))?.value : undefined;
  const allLoaded = TASK_SCOPES.every((s) => lists[s.value] !== null);
  const tab: TaskScope = picked ?? focusScope ?? "mine";

  return (
    <>
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Tasks</h2>
          <p className="text-sm text-muted-foreground">
            Work for you and your teams, created by people, by the assistant (after approval) and by workflows.
          </p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <Plus data-icon="inline-start" />
          New task
        </Button>
      </header>

      <Commitments />

      {focusId && allLoaded && !focusScope ? (
        <Alert>
          <CircleAlert aria-hidden="true" />
          <AlertTitle>This task isn&apos;t open for you anymore</AlertTitle>
          <AlertDescription>It may be done, cancelled or handed to someone else.</AlertDescription>
        </Alert>
      ) : null}

      <Tabs value={tab} onValueChange={(value) => setPicked(value as TaskScope)} className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <TabsList>
            {TASK_SCOPES.map((scope) => {
              const count = lists[scope.value]?.filter((t) => t.status === "open" || t.status === "in_progress").length ?? 0;
              return (
                <TabsTrigger key={scope.value} value={scope.value}>
                  {scope.label}
                  {count > 0 ? <span className="ml-1 text-xs text-muted-foreground tabular-nums">{count}</span> : null}
                </TabsTrigger>
              );
            })}
          </TabsList>
          <RefreshingHint active={TASK_SCOPES.some((s) => { const q = queries[s.value].state; return q.status === "success" && q.refreshing; })} />
        </div>
        {TASK_SCOPES.map((scope) => {
          const query = queries[scope.value];
          return (
            <TabsContent key={scope.value} value={scope.value}>
              {query.state.status === "loading" ? (
                <TableSkeleton rows={3} />
              ) : query.state.status === "error" ? (
                <ErrorAlert error={query.state.error} onRetry={() => void query.refetch()} />
              ) : query.state.data.length === 0 ? (
                <EmptyState icon={ListTodo} title="No tasks here" description={scope.empty} />
              ) : (
                <ul className="flex flex-col divide-y rounded-xl border">
                  {sortTasks(query.state.data).map((task) => (
                    <TaskRow key={task.id} task={task} scope={scope.value} names={names} focused={task.id === focusId} />
                  ))}
                </ul>
              )}
            </TabsContent>
          );
        })}
      </Tabs>

      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent className="sm:max-w-lg">
          {creating ? <CreateTask me={me} isAdmin={isAdmin} departments={departmentChoices} onDone={() => setCreating(false)} /> : null}
        </DialogContent>
      </Dialog>
    </>
  );
}

const ORIGIN: Record<string, { label: string; icon: typeof Bot }> = {
  assistant: { label: "From the assistant", icon: Bot },
  workflow: { label: "From a workflow", icon: Workflow },
};

function TaskRow({ task, scope, names, focused }: { task: Task; scope: TaskScope; names: Names; focused: boolean }) {
  const [saving, setSaving] = useState(false);
  const ref = useRef<HTMLLIElement>(null);
  const due = dueText(task.due_at);
  const origin = typeof task.origin.via === "string" ? ORIGIN[task.origin.via] : undefined;
  const closed = task.status === "done" || task.status === "cancelled";

  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ block: "center" });
  }, [focused]);

  async function update(body: { status?: TaskStatus; assignee_id?: string }, done: string) {
    setSaving(true);
    try {
      await workApi.updateTask(task.id, body);
      await invalidateQueries(workKeys.tasks);
      toast({ title: done, description: task.title });
    } catch (error) {
      const message = errorMessage(error);
      toast({ title: message.title, description: message.description, tone: "error" });
    } finally {
      setSaving(false);
    }
  }

  return (
    <li ref={ref} className={cn("flex flex-wrap items-start gap-3 px-4 py-3", focused && "bg-sky-500/5 ring-2 ring-sky-500/40 ring-inset", closed && "opacity-70")}>
      <div className="min-w-0 flex-1">
        <p className={cn("font-medium break-words", closed && "line-through decoration-muted-foreground/50")}>{task.title}</p>
        {task.description ? <p className="line-clamp-2 text-sm whitespace-pre-line text-muted-foreground">{task.description}</p> : null}
        <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span>For: {assigneeText(task, names)}</span>
          {due ? (
            <span className={cn("flex items-center gap-1", due.overdue && !closed && "text-destructive")}>
              <CalendarClock className="size-3" aria-hidden="true" />
              {due.text}
            </span>
          ) : null}
          <span title={formatDateTime(task.created_at)}>Created {formatRelative(task.created_at)}</span>
          {origin ? (
            <Badge variant="secondary" className="font-normal">
              <origin.icon data-icon="inline-start" aria-hidden="true" />
              {origin.label}
            </Badge>
          ) : null}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {scope === "queues" && !task.assignee_id ? (
          <Button variant="outline" size="sm" disabled={saving} onClick={() => void update({ assignee_id: names.meId }, "Claimed: it's yours now")}>
            <Hand data-icon="inline-start" />
            Claim
          </Button>
        ) : null}
        <SimpleSelect
          size="sm"
          ariaLabel={`Status of ${task.title}`}
          value={task.status}
          disabled={saving}
          onChange={(status) => void update({ status }, `Marked ${TASK_STATUS_LABELS[status].toLowerCase()}`)}
          options={TASK_STATUSES.map((s) => ({ value: s, label: TASK_STATUS_LABELS[s] }))}
        />
      </div>
    </li>
  );
}

function CreateTask({
  me,
  isAdmin,
  departments,
  onDone,
}: {
  me: Me;
  isAdmin: boolean;
  departments: { id: string; slug: string; name: string }[];
  onDone: () => void;
}) {
  const [form, setForm] = useState<TaskForm>(emptyTaskForm);
  const [person, setPerson] = useState<AdminUser | null>(null);
  const [showErrors, setShowErrors] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const errors = validateTaskForm(form);
  const shown = (key: keyof typeof errors) => (showErrors ? (errors[key] ?? null) : null);
  const patch = (change: Partial<TaskForm>) => setForm((f) => ({ ...f, ...change }));

  const assignOptions: { value: AssignTo; label: string }[] = [
    { value: "me", label: "Me" },
    ...(departments.length > 0 ? [{ value: "queue" as const, label: "A team queue" }] : []),
    ...(isAdmin ? [{ value: "person" as const, label: "Someone else" }] : []),
    { value: "nobody", label: "Nobody yet" },
  ];

  async function save() {
    setShowErrors(true);
    if (Object.keys(errors).length > 0) return;
    setPending(true);
    setError(null);
    try {
      const task = await workApi.createTask(toTaskCreate(form, me.id));
      await invalidateQueries(workKeys.tasks);
      toast({ title: "Task created", description: task.title });
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
        <DialogTitle>New task</DialogTitle>
        <DialogDescription>Assign it to yourself, to a team&apos;s queue, or leave it for later.</DialogDescription>
      </DialogHeader>
      <div className="flex flex-col gap-4">
        <Field label="Title" error={shown("title")}>
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              autoFocus
              maxLength={TASK_LIMITS.titleMax}
              value={form.title}
              onChange={(e) => patch({ title: e.target.value })}
              aria-describedby={describedBy}
              aria-invalid={invalid || undefined}
            />
          )}
        </Field>
        <Field label="Description (optional)">
          {({ id }) => (
            <Textarea id={id} rows={3} maxLength={TASK_LIMITS.descriptionMax} value={form.description} onChange={(e) => patch({ description: e.target.value })} />
          )}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Assign to">
            {({ id }) => <SimpleSelect id={id} value={form.assignTo} onChange={(assignTo) => patch({ assignTo })} options={assignOptions} />}
          </Field>
          <Field label="Due (optional)" error={shown("due")}>
            {({ id, describedBy, invalid }) => (
              <Input id={id} type="date" value={form.due} onChange={(e) => patch({ due: e.target.value })} aria-describedby={describedBy} aria-invalid={invalid || undefined} />
            )}
          </Field>
        </div>
        {form.assignTo === "queue" ? (
          <Field label="Team" error={shown("department")} hint="Anyone in that department can claim it.">
            {({ id }) => (
              <SimpleSelect
                id={id}
                value={form.department}
                onChange={(department) => patch({ department })}
                options={[{ value: "", label: "Choose a team", disabled: true }, ...departments.map((d) => ({ value: d.slug, label: d.name }))]}
              />
            )}
          </Field>
        ) : null}
        {form.assignTo === "person" ? (
          <div className="flex flex-col gap-2">
            {person ? (
              <div className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm">
                <span>
                  {person.display_name} <span className="text-muted-foreground">{person.email}</span>
                </span>
                <Button type="button" variant="ghost" size="xs" onClick={() => { setPerson(null); patch({ personId: "" }); }}>
                  Change
                </Button>
              </div>
            ) : (
              <PersonPicker
                exclude={new Set()}
                label="Find a person"
                onPick={(user) => {
                  setPerson(user);
                  patch({ personId: user.id });
                }}
              />
            )}
            {shown("personId") ? <p className="text-xs text-destructive">{errors.personId}</p> : null}
          </div>
        ) : null}
      </div>
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? "Creating…" : "Create task"}
        </Button>
      </DialogFooter>
    </form>
  );
}
