"use client";

import {
  ArrowLeft,
  CalendarClock,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  Info,
  ListTodo,
  LoaderCircle,
  PencilLine,
  Play,
  ScrollText,
  Trash2,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { SignedInGate } from "@/components/auth/signed-in-gate";
import { ConfirmDialog } from "@/components/data/confirm-dialog";
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
import { formatDateTime } from "@/lib/format";
import { meetingsApi, meetingsKeys, type ActionItem, type Decision, type Meeting } from "@/lib/meetings/api";
import {
  actionEditPayload,
  actionForm,
  activeSegment,
  dueOf,
  formatDay,
  formatDuration,
  formatTimestamp,
  isProcessing,
  jumpTarget,
  MEETING_DELETION_KEYS,
  meetingDeletionText,
  meetingErrorMessage,
  meetingPeople,
  mergePeople,
  ownerText,
  panelScrollTop,
  parseStartParam,
  personOptions,
  pipelineSteps,
  speakerChanges,
  speakerName,
  stepLabel,
  untimedCount,
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
  if (state.status === "error") {
    if (state.error instanceof ApiError && state.error.status === 404) {
      return (
        <EmptyState
          icon={CalendarClock}
          title="Meeting not found"
          description="It doesn't exist, or it is in a collection you can't view. Meetings follow their collection's access."
        />
      );
    }
    return <ErrorAlert error={state.error} onRetry={() => void refetch()} />;
  }
  const m = state.data;

  return (
    <>
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold tracking-tight break-words">{m.title}</h2>
          <p className="flex flex-wrap gap-x-3 text-sm text-muted-foreground">
            <span title={`Uploaded ${formatDateTime(m.created_at)}`}>{formatDay(m.held_on)}</span>
            {formatDuration(m.duration_s) ? <span>{formatDuration(m.duration_s)}</span> : null}
            <span className="font-mono text-xs leading-5 break-all">{m.filename}</span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <MeetingStatusBadge status={m.status} />
          {m.can_edit ? <DeleteMeeting meeting={m} /> : null}
        </div>
      </header>
      {m.status === "ready" ? (
        <ReadyMeeting meeting={m} me={me} startAt={startAt} />
      ) : m.status === "failed" ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertTitle>This meeting couldn&apos;t be processed</AlertTitle>
          <AlertDescription>
            <p>{m.error || "Something went wrong while processing it."}</p>
            <p>
              Fix the cause, then{" "}
              <Link href="/meetings" className="underline underline-offset-2">
                upload it again
              </Link>
              .
            </p>
          </AlertDescription>
        </Alert>
      ) : (
        <Processing meeting={m} />
      )}
    </>
  );
}

/** The uploader and the collection's editors (`can_edit`) may delete a meeting. */
function DeleteMeeting({ meeting }: { meeting: Meeting }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const tasks = meeting.action_items.filter((item) => item.task_id).length;
  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <Trash2 data-icon="inline-start" />
        Delete
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={`Delete “${meeting.title}”?`}
        description={meetingDeletionText(tasks)}
        confirmLabel="Delete meeting"
        onConfirm={async () => {
          await meetingsApi.remove(meeting.id);
          // Not this meeting's own entry: it would refetch, and say "not found", before we leave.
          await Promise.all(MEETING_DELETION_KEYS.map((key) => invalidateQueries(key)));
          toast({ title: `“${meeting.title}” deleted` });
          router.replace("/meetings");
        }}
      />
    </>
  );
}

function Processing({ meeting }: { meeting: Meeting }) {
  return (
    <section className="flex flex-col gap-2 rounded-xl border p-4" aria-labelledby="processing-heading">
      <h3 id="processing-heading" className="text-sm font-medium">
        Processing
      </h3>
      <ol className="flex flex-col gap-2" aria-live="polite">
        {pipelineSteps(meeting.status).map((step) => (
          <li key={step.status} className={cn("flex items-center gap-2 text-sm", step.state === "todo" && "text-muted-foreground")}>
            {step.state === "done" ? (
              <CircleCheck className="size-4 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
            ) : step.state === "current" ? (
              <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <CircleDashed className="size-4" aria-hidden="true" />
            )}
            <span>
              {stepLabel(step.status, meeting.has_media)}
              <span className="sr-only">{step.state === "done" ? " (done)" : step.state === "current" ? " (in progress)" : " (to do)"}</span>
            </span>
          </li>
        ))}
      </ol>
      <p className="text-xs text-muted-foreground">This page updates by itself. You&apos;ll also get a notification when the notes are ready.</p>
    </section>
  );
}

const reducedMotion = () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export function ReadyMeeting({ meeting, me, startAt }: { meeting: Meeting; me: Me; startAt: number | null }) {
  const audio = useRef<HTMLAudioElement>(null);
  const panel = useRef<HTMLElement>(null);
  const transcript = useRef<HTMLOListElement>(null);
  // Only the playing segment is state (not the time), so playback doesn't re-render every segment.
  const [current, setCurrent] = useState<string | null>(null);
  // A commitment link (?t=271.4) opens the meeting at that moment, its segment highlighted.
  const [startSegment] = useState(() => (startAt !== null ? activeSegment(meeting.segments, startAt) : null));
  const [flash, setFlash] = useState<string | null>(startSegment);
  const flashTimer = useRef<number | undefined>(undefined);

  // Editors pick owners and speakers from the colleague directory, plus anyone this meeting already
  // linked (guests can't use the directory; a linked name never disappears from its own select).
  const directoryEnabled = meeting.can_edit && canUseDirectory(me.org_role);
  const directory = usePeople(directoryEnabled);
  const people: PersonOption[] = useMemo(
    () => mergePeople(meetingPeople(meeting, { id: me.id, name: me.display_name }), directory.people),
    [meeting, me.id, me.display_name, directory.people],
  );

  const segmentEl = (segment: string) => transcript.current?.querySelector<HTMLElement>(`[data-segment="${CSS.escape(segment)}"]`) ?? null;

  /** Scrolls the transcript panel (not the page) to a segment. */
  const scrollToSegment = useCallback((segment: string, block: "center" | "nearest", smooth: boolean) => {
    const list = transcript.current;
    const el = list?.querySelector<HTMLElement>(`[data-segment="${CSS.escape(segment)}"]`);
    if (!list || !el) return;
    const box = el.getBoundingClientRect();
    const top = panelScrollTop({ top: box.top - list.getBoundingClientRect().top, height: box.height }, list, block);
    if (top !== null) list.scrollTo({ top, behavior: smooth && !reducedMotion() ? "smooth" : "auto" });
  }, []);

  const highlight = useCallback((segment: string) => {
    setFlash(segment);
    window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlash(null), 2500);
  }, []);
  useEffect(() => () => window.clearTimeout(flashTimer.current), []);

  function jump(target: JumpTarget) {
    if (target.kind === "media") {
      const player = audio.current;
      if (player) {
        player.currentTime = target.seconds;
        void player.play().catch(() => undefined); // the browser may refuse to autoplay; the seek still happened
      }
      if (target.segment) {
        scrollToSegment(target.segment, "center", true);
        highlight(target.segment);
      }
      return;
    }
    // A transcript upload: bring the transcript into view and move focus to the segment.
    panel.current?.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
    scrollToSegment(target.segment, "center", true);
    highlight(target.segment);
    segmentEl(target.segment)?.focus({ preventScroll: true });
  }

  const opened = useRef(false);
  useEffect(() => {
    if (opened.current || startAt === null) return;
    opened.current = true;
    if (meeting.has_media && audio.current) audio.current.currentTime = startAt; // applied once metadata loads
    if (startSegment) {
      scrollToSegment(startSegment, "center", false);
      flashTimer.current = window.setTimeout(() => setFlash(null), 2500);
    }
  }, [startAt, startSegment, meeting.has_media, scrollToSegment]);

  // Follow playback: keep the playing segment visible in the panel.
  useEffect(() => {
    if (current && audio.current && !audio.current.paused) scrollToSegment(current, "nearest", true);
  }, [current, scrollToSegment]);

  const untimed = untimedCount(meeting);

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
      <div className="flex min-w-0 flex-col gap-6">
        <section aria-labelledby="summary-heading" className="flex flex-col gap-2">
          <h3 id="summary-heading" className="font-medium">
            Summary
          </h3>
          <p className="text-sm leading-relaxed whitespace-pre-line">{meeting.summary || "No summary was extracted."}</p>
        </section>

        <section aria-labelledby="decisions-heading" className="flex flex-col gap-2">
          <h3 id="decisions-heading" className="font-medium">
            Decisions <span className="text-sm font-normal text-muted-foreground">({meeting.decisions.length})</span>
          </h3>
          {meeting.decisions.length === 0 ? (
            <p className="text-sm text-muted-foreground">No decisions were found in this meeting.</p>
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
            <p className="text-sm text-muted-foreground">No one committed to anything in this meeting.</p>
          ) : (
            <ol className="flex flex-col gap-2">
              {meeting.action_items.map((item, index) => (
                <ActionRow key={index} meeting={meeting} item={item} index={index} meId={me.id} people={people} onJump={jump} />
              ))}
            </ol>
          )}
          {untimed > 0 ? (
            <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
              <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              {untimed === 1 ? "One item has" : `${untimed} items have`} no timestamp: the extraction pointed at a part of the transcript
              that doesn&apos;t exist, so no time is shown rather than a guessed one.
            </p>
          ) : null}
        </section>

        {meeting.can_edit && Object.keys(meeting.speakers).length > 0 ? (
          <SpeakerMapping
            key={JSON.stringify(meeting.speakers)}
            meeting={meeting}
            people={people}
            directoryError={directoryEnabled ? directory.error : null}
          />
        ) : null}
      </div>

      <div className="flex min-w-0 flex-col gap-3 lg:sticky lg:top-18 lg:max-h-[calc(100vh-6rem)] lg:self-start">
        {meeting.has_media ? (
          <audio
            ref={audio}
            controls
            preload="metadata"
            src={meetingsApi.mediaUrl(meeting.id)}
            className="w-full shrink-0"
            onTimeUpdate={(e) => setCurrent(activeSegment(meeting.segments, e.currentTarget.currentTime))}
            aria-label={`Recording of ${meeting.title}`}
          >
            Your browser can&apos;t play this recording.
          </audio>
        ) : null}
        <section ref={panel} aria-labelledby="transcript-heading" className="flex min-h-0 flex-col rounded-xl border">
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
                  tabIndex={-1}
                  aria-current={current === segment.id ? "true" : undefined}
                  className={cn(
                    "flex gap-2 rounded-md px-2 py-1.5 transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
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
      aria-label={hasMedia ? `Play the recording from ${label}` : `Show ${label} in the transcript`}
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
  const correctButton = useRef<HTMLButtonElement>(null);
  const due = dueOf(item.due);
  const isTask = Boolean(item.task_id);

  function open() {
    setForm(actionForm(item)); // the item may have changed since (speaker mapping re-links owners)
    setErrors({});
    setError(null);
    setEditing(true);
  }

  function close() {
    setEditing(false);
    setErrors({});
    setError(null);
    window.requestAnimationFrame(() => correctButton.current?.focus());
  }

  async function save() {
    const payload = actionEditPayload(item, form);
    if (!payload.ok) {
      setErrors(payload.errors);
      return;
    }
    setErrors({});
    if (!payload.body) {
      close();
      return;
    }
    setPending("save");
    setError(null);
    try {
      storeMeeting(await meetingsApi.editAction(meeting.id, index, payload.body));
      close();
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
      toast({
        title: "Task created",
        description: item.owner_user_id
          ? item.owner_user_id === meId
            ? "It's in your tasks."
            : `${item.owner || "Its owner"} was notified.`
          : "Nobody owns it yet: it is in your “Created by me” tasks.",
      });
    } catch (err) {
      setError(err);
      if (err instanceof ApiError && err.code === "already_a_task") void invalidateQueries(meetingsKeys.meeting(meeting.id));
    } finally {
      setPending(null);
    }
  }

  const owner = item.owner_user_id ? { id: item.owner_user_id, name: item.owner || "Current owner" } : null;

  return (
    <li className="flex flex-col gap-2 rounded-lg border px-3 py-2 text-sm">
      {editing ? (
        <form
          className="flex flex-col gap-3"
          noValidate
          aria-label={`Correct action item ${index + 1}`}
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape" && pending === null) {
              e.stopPropagation();
              close();
            }
          }}
        >
          <Field label="What needs to happen" error={errors.text ?? null}>
            {({ id, describedBy, invalid }) => (
              <Input
                id={id}
                autoFocus
                maxLength={500}
                value={form.text}
                disabled={pending !== null}
                onChange={(e) => setForm({ ...form, text: e.target.value })}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
              />
            )}
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field
              label="Owner"
              hint={item.owner && !item.owner_user_id ? `Said in the meeting: “${item.owner}” (not linked to anyone).` : undefined}
            >
              {({ id, describedBy, invalid }) => (
                <SimpleSelect
                  id={id}
                  describedBy={describedBy}
                  invalid={invalid}
                  value={form.ownerUserId}
                  disabled={pending !== null}
                  onChange={(ownerUserId) => setForm({ ...form, ownerUserId })}
                  options={personOptions(people, owner, "Nobody")}
                />
              )}
            </Field>
            <Field label="Due" error={errors.due ?? null}>
              {({ id, describedBy, invalid }) => (
                <Input
                  id={id}
                  type="date"
                  value={form.due}
                  disabled={pending !== null}
                  onChange={(e) => setForm({ ...form, due: e.target.value })}
                  aria-describedby={describedBy}
                  aria-invalid={invalid || undefined}
                />
              )}
            </Field>
          </div>
          {error ? <ErrorAlert error={error} message={meetingErrorMessage(error)} /> : null}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" disabled={pending !== null} onClick={close}>
              Cancel
            </Button>
            <Button type="submit" size="sm" disabled={pending !== null}>
              {pending === "save" ? <LoaderCircle className="animate-spin" data-icon="inline-start" aria-hidden="true" /> : null}
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
                  <span className="sr-only">Owner: </span>
                  {ownerText(item, meId)}
                  {item.owner && !item.owner_user_id ? <span>(not linked to a colleague)</span> : null}
                </span>
                {due ? (
                  <span className={cn("flex items-center gap-1", due.overdue && !isTask && "text-destructive")}>
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
                <Button ref={correctButton} type="button" variant="ghost" size="xs" disabled={pending !== null} onClick={open}>
                  <PencilLine data-icon="inline-start" aria-hidden="true" />
                  Correct
                </Button>
                <Button type="button" variant="outline" size="xs" disabled={pending !== null} onClick={() => void makeTask()}>
                  {pending === "task" ? (
                    <LoaderCircle className="animate-spin" data-icon="inline-start" aria-hidden="true" />
                  ) : (
                    <ListTodo data-icon="inline-start" aria-hidden="true" />
                  )}
                  {pending === "task" ? "Creating…" : "Make task"}
                </Button>
              </>
            ) : null}
          </div>
          {error ? <ErrorAlert error={error} message={meetingErrorMessage(error)} /> : null}
        </>
      )}
    </li>
  );
}

function SpeakerMapping({ meeting, people, directoryError }: { meeting: Meeting; people: PersonOption[]; directoryError: unknown }) {
  const labels = Object.keys(meeting.speakers).sort((a, b) => a.localeCompare(b));
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
      toast({ title: "Speakers saved", description: "Action items owned by those speakers are now linked to them." });
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <section aria-labelledby="speakers-heading" className="flex flex-col gap-3 rounded-xl border p-4">
      <div>
        <h3 id="speakers-heading" className="font-medium">
          Who is who
        </h3>
        <p className="text-xs text-muted-foreground">
          Link each speaker in the transcript to a colleague. Their action items are linked to that person, so a task made from one
          reaches them.
        </p>
      </div>
      {directoryError ? (
        <p className="text-xs text-muted-foreground">
          The colleague directory didn&apos;t load, so only people this meeting already knows are listed.
        </p>
      ) : null}
      <ul className="flex flex-col gap-2">
        {labels.map((label) => {
          const speaker = meeting.speakers[label];
          return (
            <li key={label} className="flex flex-wrap items-center justify-between gap-2">
              <span className="min-w-0 truncate text-sm" title={label}>
                {label}
              </span>
              <SimpleSelect
                size="sm"
                ariaLabel={`Colleague for ${label}`}
                value={chosen[label] ?? ""}
                disabled={pending}
                onChange={(value) => setChosen({ ...chosen, [label]: value || null })}
                options={personOptions(people, speaker.user_id ? { id: speaker.user_id, name: speaker.name } : null, "Not a colleague")}
              />
            </li>
          );
        })}
      </ul>
      {error ? <ErrorAlert error={error} message={meetingErrorMessage(error)} /> : null}
      <Button type="button" size="sm" className="w-fit self-end" disabled={!dirty || pending} onClick={() => void save()}>
        {pending ? <LoaderCircle className="animate-spin" data-icon="inline-start" aria-hidden="true" /> : null}
        {pending ? "Saving…" : "Save speakers"}
      </Button>
    </section>
  );
}
