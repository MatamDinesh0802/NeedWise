"use client";

import { ArrowLeft, CalendarClock, CircleAlert, CircleCheck, CircleDashed, ListTodo, LoaderCircle, PencilLine, Play, ScrollText, Users } from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { SignedInGate } from "@/components/auth/signed-in-gate";
import { Field, SimpleSelect } from "@/components/data/form";
import { EmptyState, ErrorAlert, TableSkeleton } from "@/components/data/states";
import { MeetingStatusBadge } from "@/components/meetings/meetings-view";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { usePeople } from "@/hooks/use-people";
import { usePolling } from "@/hooks/use-polling";
import { ApiError } from "@/lib/api";
import { errorMessage } from "@/lib/error-messages";
import { formatDate, formatDateTime } from "@/lib/format";
import { meetingsApi, meetingsKeys, type ActionItem, type Decision, type Meeting } from "@/lib/meetings/api";
import {
  actionEditPayload,
  actionForm,
  activeSegment,
  formatDuration,
  formatTimestamp,
  isProcessing,
  jumpTarget,
  meetingPeople,
  ownerText,
  parseStartParam,
  pipelineSteps,
  speakerChanges,
  speakerName,
  STATUS_LABELS,
  type ActionForm,
  type JumpTarget,
  type PersonOption,
} from "@/lib/meetings/meetings";
import { canUseDirectory } from "@/lib/people";
import type { Me } from "@/lib/session";
import { getQueryCache } from "@/lib/stores/query-cache";
import { toast } from "@/lib/stores/toasts";
import { cn } from "@/lib/utils";
import { workKeys } from "@/lib/work/api";
import { dueText } from "@/lib/work/tasks";

/** /meetings/[id]: the id and the optional ?t= start are runtime data (behind Suspense). */
export function MeetingRoute() {
  const { id } = useParams<{ id: string }>();
  const startAt = parseStartParam(useSearchParams().get("t"));
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4">
      <Link href="/meetings" className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "w-fit")}>
        <ArrowLeft data-icon="inline-start" />
        Meetings
      </Link>
      <SignedInGate purpose="see this meeting">{(me) => <MeetingDetail key={id} id={id} me={me} startAt={startAt} />}</SignedInGate>
    </div>
  );
}

function MeetingDetail({ id, me, startAt }: { id: string; me: Me; startAt: number | null }) {
  const key = meetingsKeys.meeting(id);
  const { state, refetch } = useApiQuery(key, () => meetingsApi.get(id));
  const meeting = state.status === "success" ? state.data : null;
  usePolling(key, meeting !== null && isProcessing(meeting.status), 3000);

  if (state.status === "loading") return <TableSkeleton rows={4} />;
  if (state.status === "error") return <ErrorAlert error={state.error} onRetry={() => void refetch()} />;
  const m = state.data;

  return (
    <>
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold tracking-tight break-words">{m.title}</h2>
          <p className="flex flex-wrap gap-x-3 text-sm text-muted-foreground">
            <span title={formatDateTime(m.created_at)}>{formatDate(m.created_at)}</span>
            {formatDuration(m.duration_s) ? <span>{formatDuration(m.duration_s)}</span> : null}
            <span className="font-mono text-xs leading-5">{m.filename}</span>
          </p>
        </div>
        <MeetingStatusBadge status={m.status} />
      </header>
      {m.status === "ready" ? (
        <ReadyMeeting meeting={m} me={me} startAt={startAt} />
      ) : m.status === "failed" ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertTitle>This meeting couldn&apos;t be processed</AlertTitle>
          <AlertDescription>{m.error ?? "Something went wrong."} Upload it again once the problem is fixed.</AlertDescription>
        </Alert>
      ) : (
        <Processing meeting={m} />
      )}
    </>
  );
}

function Processing({ meeting }: { meeting: Meeting }) {
  return (
    <ol className="flex flex-col gap-2 rounded-xl border p-4" aria-label="Processing">
      {pipelineSteps(meeting.status).map((step) => (
        <li key={step.status} className={cn("flex items-center gap-2 text-sm", step.state === "todo" && "text-muted-foreground")}>
          {step.state === "done" ? (
            <CircleCheck className="size-4 text-emerald-600 dark:text-emerald-400" aria-label="done" />
          ) : step.state === "current" ? (
            <LoaderCircle className="size-4 animate-spin" aria-label="in progress" />
          ) : (
            <CircleDashed className="size-4" aria-label="to do" />
          )}
          {STATUS_LABELS[step.status]}
        </li>
      ))}
    </ol>
  );
}

function ReadyMeeting({ meeting, me, startAt }: { meeting: Meeting; me: Me; startAt: number | null }) {
  const audio = useRef<HTMLAudioElement>(null);
  const transcript = useRef<HTMLOListElement>(null);
  const [time, setTime] = useState<number | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  // Editors pick owners and speakers from the colleague directory (plus anyone this meeting
  // already linked, so a name never disappears from its own select).
  const directory = usePeople(meeting.can_edit && canUseDirectory(me.org_role));
  const people: PersonOption[] = useMemo(() => {
    const all = new Map(meetingPeople(meeting, { id: me.id, name: me.display_name }).map((p) => [p.id, p.name]));
    for (const person of directory.people) all.set(person.id, person.display_name);
    return [...all].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
  }, [meeting, me.id, me.display_name, directory.people]);

  const current = time !== null ? activeSegment(meeting.segments, time) : null;

  function showSegment(segment: string) {
    const el = transcript.current?.querySelector<HTMLElement>(`[data-segment="${CSS.escape(segment)}"]`);
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
    setFlash(segment);
    window.setTimeout(() => setFlash((f) => (f === segment ? null : f)), 2000);
  }

  function jump(target: JumpTarget) {
    if (target.kind === "media" && audio.current) {
      audio.current.currentTime = target.seconds;
      void audio.current.play().catch(() => undefined); // autoplay rules may refuse; the seek still happened
      if (target.segment) showSegment(target.segment);
    } else if (target.kind === "segment") {
      showSegment(target.segment);
    }
  }

  // A commitment link (?t=271) opens the meeting at that moment.
  const started = useRef(false);
  useEffect(() => {
    if (started.current || startAt === null) return;
    started.current = true;
    const segment = activeSegment(meeting.segments, startAt);
    if (meeting.has_media && audio.current) {
      audio.current.currentTime = startAt;
    }
    if (segment) {
      const el = transcript.current?.querySelector<HTMLElement>(`[data-segment="${CSS.escape(segment)}"]`);
      el?.scrollIntoView({ block: "center" });
    }
  }, [startAt, meeting.has_media, meeting.segments]);

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
      <div className="flex min-w-0 flex-col gap-6">
        <section aria-labelledby="summary-heading" className="flex flex-col gap-2">
          <h3 id="summary-heading" className="font-medium">
            Summary
          </h3>
          <p className="text-sm leading-relaxed whitespace-pre-line">{meeting.summary || "No summary."}</p>
        </section>

        <section aria-labelledby="decisions-heading" className="flex flex-col gap-2">
          <h3 id="decisions-heading" className="font-medium">
            Decisions <span className="text-sm font-normal text-muted-foreground">({meeting.decisions.length})</span>
          </h3>
          {meeting.decisions.length === 0 ? (
            <p className="text-sm text-muted-foreground">No decisions were made.</p>
          ) : (
            <ol className="flex flex-col gap-2">
              {meeting.decisions.map((decision, index) => (
                <li key={index} className="flex items-start gap-3 rounded-lg border px-3 py-2 text-sm">
                  <CircleCheck className="mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
                  <p className="min-w-0 flex-1">{decision.text}</p>
                  <Timestamp item={decision} hasMedia={meeting.has_media} onJump={jump} />
                </li>
              ))}
            </ol>
          )}
        </section>

        <section aria-labelledby="actions-heading" className="flex flex-col gap-2">
          <h3 id="actions-heading" className="font-medium">
            Action items <span className="text-sm font-normal text-muted-foreground">({meeting.action_items.length})</span>
          </h3>
          {meeting.action_items.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nobody committed to anything.</p>
          ) : (
            <ol className="flex flex-col gap-2">
              {meeting.action_items.map((item, index) => (
                <ActionRow key={index} meeting={meeting} item={item} index={index} meId={me.id} people={people} onJump={jump} />
              ))}
            </ol>
          )}
        </section>

        {meeting.can_edit && Object.keys(meeting.speakers).length > 0 ? <SpeakerMapping meeting={meeting} people={people} /> : null}
      </div>

      <div className="flex min-w-0 flex-col gap-3 lg:sticky lg:top-18 lg:max-h-[calc(100vh-6rem)]">
        {meeting.has_media ? (
          <audio
            ref={audio}
            controls
            preload="metadata"
            src={meetingsApi.mediaUrl(meeting.id)}
            className="w-full"
            onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
            aria-label={`Recording of ${meeting.title}`}
          />
        ) : null}
        <section aria-labelledby="transcript-heading" className="flex min-h-0 flex-col rounded-xl border">
          <h3 id="transcript-heading" className="flex items-center gap-2 border-b px-3 py-2 text-sm font-medium">
            <ScrollText className="size-4 text-muted-foreground" aria-hidden="true" />
            Transcript
            <span className="font-normal text-muted-foreground">({meeting.segments.length} segments)</span>
          </h3>
          {meeting.segments.length === 0 ? (
            <div className="p-3">
              <EmptyState icon={ScrollText} title="No transcript" />
            </div>
          ) : (
            <ol ref={transcript} className="max-h-[60vh] overflow-y-auto p-2 text-sm lg:max-h-none lg:min-h-0 lg:flex-1">
              {meeting.segments.map((segment) => (
                <li
                  key={segment.id}
                  data-segment={segment.id}
                  aria-current={current === segment.id ? "true" : undefined}
                  className={cn(
                    "flex gap-2 rounded-md px-2 py-1.5 transition-colors",
                    current === segment.id && "bg-sky-500/10",
                    flash === segment.id && "bg-amber-500/20",
                  )}
                >
                  {meeting.has_media ? (
                    <button
                      type="button"
                      className="h-fit shrink-0 rounded font-mono text-xs text-muted-foreground tabular-nums hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
                      aria-label={`Play from ${formatTimestamp(segment.start)}`}
                      onClick={() => jump({ kind: "media", seconds: segment.start, segment: segment.id })}
                    >
                      {formatTimestamp(segment.start)}
                    </button>
                  ) : (
                    <span className="shrink-0 font-mono text-xs text-muted-foreground tabular-nums">{formatTimestamp(segment.start)}</span>
                  )}
                  <p className="min-w-0">
                    {segment.speaker ? <span className="font-medium">{speakerName(segment.speaker, meeting.speakers)}: </span> : null}
                    {segment.text}
                  </p>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
    </div>
  );
}

function Timestamp({ item, hasMedia, onJump }: { item: Pick<Decision, "segment" | "start">; hasMedia: boolean; onJump: (target: JumpTarget) => void }) {
  const target = jumpTarget(item, hasMedia);
  if (!target || item.start == null) return null; // no valid segment: no timestamp, never a made-up one
  const label = formatTimestamp(item.start);
  return (
    <Button
      type="button"
      variant="outline"
      size="xs"
      className="shrink-0 font-mono tabular-nums"
      aria-label={hasMedia ? `Play from ${label}` : `Show ${label} in the transcript`}
      title={hasMedia ? "Play from here" : "Show in the transcript"}
      onClick={() => onJump(target)}
    >
      {hasMedia ? <Play data-icon="inline-start" aria-hidden="true" /> : null}
      {label}
    </Button>
  );
}

/** After a mutation the API returns the whole meeting: store it, and refresh what depends on it. */
function storeMeeting(meeting: Meeting) {
  getQueryCache().setData<Meeting>(meetingsKeys.meeting(meeting.id), () => meeting);
  void invalidateQueries(meetingsKeys.commitments);
}

function ActionRow({
  meeting,
  item,
  index,
  meId,
  people,
  onJump,
}: {
  meeting: Meeting;
  item: ActionItem;
  index: number;
  meId: string;
  people: PersonOption[];
  onJump: (target: JumpTarget) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<ActionForm>(() => actionForm(item));
  const [errors, setErrors] = useState<Partial<Record<keyof ActionForm, string>>>({});
  const [pending, setPending] = useState<"save" | "task" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const due = item.due ? dueText(`${item.due}T17:00:00Z`) : null;
  const isTask = Boolean(item.task_id);

  async function save() {
    const payload = actionEditPayload(item, form);
    if (!payload.ok) {
      setErrors(payload.errors);
      return;
    }
    setErrors({});
    if (!payload.body) {
      setEditing(false);
      return;
    }
    setPending("save");
    setError(null);
    try {
      storeMeeting(await meetingsApi.editAction(meeting.id, index, payload.body));
      setEditing(false);
      toast({ title: "Action item corrected" });
    } catch (err) {
      setError(err);
      if (err instanceof ApiError && err.code === "already_a_task") void invalidateQueries(meetingsKeys.meeting(meeting.id));
    } finally {
      setPending(null);
    }
  }

  async function makeTask() {
    setPending("task");
    setError(null);
    try {
      storeMeeting(await meetingsApi.makeTask(meeting.id, index));
      await invalidateQueries(workKeys.tasks);
      toast({ title: "Task created", description: item.owner_user_id ? "Its owner was notified." : "Nobody owns it yet: assign it on the task." });
    } catch (err) {
      setError(err);
      if (err instanceof ApiError && err.code === "already_a_task") void invalidateQueries(meetingsKeys.meeting(meeting.id));
    } finally {
      setPending(null);
    }
  }

  const ownerOptions = [{ value: "", label: "Nobody" }, ...people.map((p) => ({ value: p.id, label: p.name }))];
  if (item.owner_user_id && !people.some((p) => p.id === item.owner_user_id)) ownerOptions.push({ value: item.owner_user_id, label: item.owner || "Current owner" });

  return (
    <li className="flex flex-col gap-2 rounded-lg border px-3 py-2 text-sm">
      {editing ? (
        <form
          className="flex flex-col gap-3"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <Field label="What needs to happen" error={errors.text ?? null}>
            {({ id, describedBy, invalid }) => (
              <Input id={id} maxLength={500} value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })} aria-describedby={describedBy} aria-invalid={invalid || undefined} />
            )}
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Owner" hint={`Said in the meeting: “${item.owner || "nobody"}”`}>
              {({ id }) => <SimpleSelect id={id} value={form.ownerUserId} onChange={(ownerUserId) => setForm({ ...form, ownerUserId })} options={ownerOptions} />}
            </Field>
            <Field label="Due" error={errors.due ?? null}>
              {({ id, describedBy, invalid }) => (
                <Input id={id} type="date" value={form.due} onChange={(e) => setForm({ ...form, due: e.target.value })} aria-describedby={describedBy} aria-invalid={invalid || undefined} />
              )}
            </Field>
          </div>
          {error ? <ErrorAlert error={error} /> : null}
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={pending !== null}
              onClick={() => {
                setEditing(false);
                setForm(actionForm(item));
                setErrors({});
                setError(null);
              }}
            >
              Cancel
            </Button>
            <Button type="submit" size="sm" disabled={pending !== null}>
              {pending === "save" ? "Saving…" : "Save"}
            </Button>
          </div>
        </form>
      ) : (
        <>
          <div className="flex items-start gap-3">
            <ListTodo className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p>{item.text}</p>
              <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <span className="flex items-center gap-1">
                  <Users className="size-3" aria-hidden="true" />
                  {ownerText(item, meId)}
                  {item.owner && !item.owner_user_id ? <span title="Not linked to a colleague">(not linked)</span> : null}
                </span>
                {due ? (
                  <span className={cn("flex items-center gap-1", due.overdue && "text-destructive")}>
                    <CalendarClock className="size-3" aria-hidden="true" />
                    {due.text}
                  </span>
                ) : (
                  <span>No deadline</span>
                )}
              </p>
            </div>
            <Timestamp item={item} hasMedia={meeting.has_media} onJump={onJump} />
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {isTask ? (
              <Link href={`/tasks/${item.task_id}`} className={cn(buttonVariants({ variant: "secondary", size: "xs" }))}>
                <CircleCheck data-icon="inline-start" aria-hidden="true" />
                Task created · open it
              </Link>
            ) : meeting.can_edit ? (
              <>
                <Button type="button" variant="ghost" size="xs" disabled={pending !== null} onClick={() => setEditing(true)}>
                  <PencilLine data-icon="inline-start" />
                  Correct
                </Button>
                <Button type="button" variant="outline" size="xs" disabled={pending !== null} onClick={() => void makeTask()}>
                  {pending === "task" ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <ListTodo data-icon="inline-start" />}
                  Make task
                </Button>
              </>
            ) : null}
          </div>
          {error && !editing ? <ErrorAlert error={error} /> : null}
        </>
      )}
    </li>
  );
}

function SpeakerMapping({ meeting, people }: { meeting: Meeting; people: PersonOption[] }) {
  const labels = Object.keys(meeting.speakers).sort();
  const [chosen, setChosen] = useState<Record<string, string | null>>(() =>
    Object.fromEntries(labels.map((label) => [label, meeting.speakers[label].user_id ?? null])),
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const changes = speakerChanges(meeting.speakers, chosen);
  const dirty = Object.keys(changes).length > 0;

  async function save() {
    setPending(true);
    setError(null);
    try {
      storeMeeting(await meetingsApi.setSpeakers(meeting.id, changes));
      toast({ title: "Speakers saved", description: "Action items said by them are linked to those people." });
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  const options = (label: string) => {
    const current = meeting.speakers[label];
    const list = [{ value: "", label: "Not a colleague" }, ...people.map((p) => ({ value: p.id, label: p.name }))];
    if (current.user_id && !people.some((p) => p.id === current.user_id)) list.push({ value: current.user_id, label: current.name });
    return list;
  };

  return (
    <section aria-labelledby="speakers-heading" className="flex flex-col gap-3 rounded-xl border p-4">
      <div>
        <h3 id="speakers-heading" className="font-medium">
          Who is who
        </h3>
        <p className="text-xs text-muted-foreground">Link each speaker to a colleague so their action items reach them.</p>
      </div>
      <ul className="grid gap-2 sm:grid-cols-2">
        {labels.map((label) => (
          <li key={label} className="flex items-center justify-between gap-2">
            <span className="min-w-0 truncate text-sm" title={label}>
              {label}
            </span>
            <SimpleSelect
              size="sm"
              ariaLabel={`Colleague for ${label}`}
              value={chosen[label] ?? ""}
              disabled={pending}
              onChange={(value) => setChosen({ ...chosen, [label]: value || null })}
              options={options(label)}
            />
          </li>
        ))}
      </ul>
      {error ? <p className="text-xs text-destructive">{errorMessage(error).title}: {errorMessage(error).description}</p> : null}
      <Button type="button" size="sm" className="w-fit self-end" disabled={!dirty || pending} onClick={() => void save()}>
        {pending ? "Saving…" : "Save speakers"}
      </Button>
    </section>
  );
}
