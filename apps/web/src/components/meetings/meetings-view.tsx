"use client";

import { CalendarClock, CircleAlert, FileAudio, FileText, LoaderCircle, ShieldCheck, Upload } from "lucide-react";
import Link from "next/link";
import { useId, useRef, useState } from "react";

import { SignedInGate } from "@/components/auth/signed-in-gate";
import { Field, SimpleSelect } from "@/components/data/form";
import { EmptyState, ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { usePolling } from "@/hooks/use-polling";
import { formatBytes, formatDate, formatDateTime } from "@/lib/format";
import { knowledgeApi, knowledgeKeys, type Collection } from "@/lib/knowledge/api";
import { meetingsApi, meetingsKeys, type MeetingStatus, type MeetingSummary } from "@/lib/meetings/api";
import { ACCEPT, fileKind, formatDuration, isProcessing, STATUS_LABELS, titleFromFilename, validateUpload } from "@/lib/meetings/meetings";
import { toast } from "@/lib/stores/toasts";
import { cn } from "@/lib/utils";

export const STATUS_STYLE: Record<MeetingStatus, string> = {
  pending: "bg-sky-500/15 text-sky-800 dark:text-sky-300",
  transcribing: "bg-sky-500/15 text-sky-800 dark:text-sky-300",
  extracting: "bg-sky-500/15 text-sky-800 dark:text-sky-300",
  ready: "bg-emerald-500/15 text-emerald-800 dark:text-emerald-300",
  failed: "bg-destructive/10 text-destructive",
};

export function MeetingStatusBadge({ status }: { status: MeetingStatus }) {
  return (
    <Badge variant="secondary" className={cn("shrink-0", STATUS_STYLE[status])}>
      {isProcessing(status) ? <LoaderCircle className="animate-spin" data-icon="inline-start" aria-hidden="true" /> : null}
      {STATUS_LABELS[status] ?? status}
    </Badge>
  );
}

export function MeetingsView() {
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6">
      <header>
        <h2 className="text-xl font-semibold tracking-tight">Meetings</h2>
        <p className="text-sm text-muted-foreground">
          A recording or transcript becomes a summary, decisions and action items, each linked to the moment it was said.
          Meetings follow their collection&apos;s access rules.
        </p>
      </header>
      <SignedInGate purpose="see meetings">{() => <MeetingsBoard />}</SignedInGate>
    </div>
  );
}

function MeetingsBoard() {
  const meetings = useApiQuery(meetingsKeys.list, meetingsApi.list);
  const collections = useApiQuery(knowledgeKeys.collections, knowledgeApi.listCollections);
  const list = meetings.state.status === "success" ? meetings.state.data : [];
  usePolling(meetingsKeys.list, list.some((m) => isProcessing(m.status)), 3000);

  const all = collections.state.status === "success" ? collections.state.data.items : [];
  const names = new Map(all.map((c) => [c.id, c.name]));
  const editable = all.filter((c) => c.can_edit);

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
      <section className="flex min-w-0 flex-col gap-3" aria-labelledby="meetings-list-heading">
        <div className="flex items-center justify-between gap-2">
          <h3 id="meetings-list-heading" className="font-medium">
            Recent meetings
          </h3>
          <RefreshingHint active={meetings.state.status === "success" && meetings.state.refreshing} />
        </div>
        {meetings.state.status === "loading" ? (
          <TableSkeleton rows={3} />
        ) : meetings.state.status === "error" ? (
          <ErrorAlert error={meetings.state.error} onRetry={() => void meetings.refetch()} />
        ) : list.length === 0 ? (
          <EmptyState icon={CalendarClock} title="No meetings yet" description="Upload a recording or a transcript to get its decisions and action items." />
        ) : (
          <ul className="flex flex-col divide-y rounded-xl border">
            {list.map((meeting) => (
              <MeetingRow key={meeting.id} meeting={meeting} collection={names.get(meeting.collection_id)} />
            ))}
          </ul>
        )}
      </section>
      <aside>
        {collections.state.status === "loading" ? (
          <TableSkeleton rows={2} />
        ) : collections.state.status === "error" ? (
          <ErrorAlert error={collections.state.error} onRetry={() => void collections.refetch()} />
        ) : (
          <UploadPanel collections={editable} />
        )}
      </aside>
    </div>
  );
}

function MeetingRow({ meeting, collection }: { meeting: MeetingSummary; collection: string | undefined }) {
  const duration = formatDuration(meeting.duration_s);
  return (
    <li>
      <Link href={`/meetings/${meeting.id}`} className="flex flex-col gap-1 px-4 py-3 hover:bg-muted/40 focus-visible:bg-muted/40 focus-visible:outline-none">
        <div className="flex items-start justify-between gap-3">
          <p className="min-w-0 font-medium break-words">{meeting.title}</p>
          <MeetingStatusBadge status={meeting.status} />
        </div>
        <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
          <span title={formatDateTime(meeting.created_at)}>{formatDate(meeting.created_at)}</span>
          {duration ? <span>{duration}</span> : null}
          {collection ? <span>{collection}</span> : null}
          {meeting.status === "ready" ? (
            <span>
              {meeting.decisions} {meeting.decisions === 1 ? "decision" : "decisions"} · {meeting.action_items}{" "}
              {meeting.action_items === 1 ? "action item" : "action items"}
            </span>
          ) : null}
        </p>
        {meeting.status === "failed" && meeting.error ? (
          <p className="flex items-start gap-1 text-xs text-destructive">
            <CircleAlert className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
            {meeting.error}
          </p>
        ) : null}
      </Link>
    </li>
  );
}

function UploadPanel({ collections }: { collections: Collection[] }) {
  const inputId = useId();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [collection, setCollection] = useState(collections.length === 1 ? collections[0].key : "");
  const [consent, setConsent] = useState(false);
  const [showErrors, setShowErrors] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const errors = validateUpload({ file, collection, consent });
  const kind = file ? fileKind(file.name) : null;

  if (collections.length === 0) {
    return (
      <div className="rounded-xl border p-4 text-sm text-muted-foreground">
        <p className="font-medium text-foreground">Add a meeting</p>
        <p className="mt-1">You need edit access to a collection to add meetings to it. Ask its owner or an admin.</p>
      </div>
    );
  }

  async function upload() {
    setShowErrors(true);
    if (!file || Object.keys(errors).length > 0) return;
    setPending(true);
    setError(null);
    try {
      const created = await meetingsApi.upload({ collection, filename: file.name, title: title || titleFromFilename(file.name) }, file);
      await invalidateQueries(meetingsKeys.list);
      toast({ title: `${created.title} uploaded`, description: kind === "audio" ? "Transcribing it now; a 20-minute recording takes about a minute." : "Reading the transcript now." });
      setFile(null);
      setTitle("");
      setConsent(false);
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
      className="flex flex-col gap-4 rounded-xl border bg-card p-4"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        void upload();
      }}
    >
      <div>
        <h3 className="font-medium">Add a meeting</h3>
        <p className="text-xs text-muted-foreground">A recording (mp3, m4a, wav, mp4, webm, ogg, flac, aiff, mov) or a transcript (vtt, srt, txt, md).</p>
      </div>

      <Field label="File" error={showErrors ? (errors.file ?? null) : null}>
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            ref={fileRef}
            type="file"
            accept={ACCEPT}
            disabled={pending}
            onChange={(e) => {
              const next = e.target.files?.[0] ?? null;
              setFile(next);
              if (next && !title) setTitle(titleFromFilename(next.name));
            }}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
          />
        )}
      </Field>
      {file ? (
        <p className="-mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
          {kind === "audio" ? <FileAudio className="size-3.5" aria-hidden="true" /> : <FileText className="size-3.5" aria-hidden="true" />}
          {kind === "audio" ? "Recording" : kind === "transcript" ? "Transcript" : "Unsupported"} · {formatBytes(file.size)}
        </p>
      ) : null}

      <Field label="Title">
        {({ id }) => <Input id={id} maxLength={200} value={title} disabled={pending} placeholder="Weekly operations sync" onChange={(e) => setTitle(e.target.value)} />}
      </Field>

      <Field label="Collection" hint="Everyone who can view it sees the meeting and finds the transcript in search." error={showErrors ? (errors.collection ?? null) : null}>
        {({ id }) => (
          <SimpleSelect
            id={id}
            value={collection}
            disabled={pending}
            onChange={setCollection}
            options={[{ value: "", label: "Choose a collection", disabled: true }, ...collections.map((c) => ({ value: c.key, label: c.name }))]}
          />
        )}
      </Field>

      <div className={cn("flex flex-col gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm", showErrors && errors.consent && "border-destructive")} role="group" aria-labelledby={`${inputId}-consent`}>
        <p id={`${inputId}-consent`} className="flex items-start gap-2 font-medium text-amber-900 dark:text-amber-200">
          <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          Everyone in the recording should know it is being processed.
        </p>
        <p className="text-xs text-amber-900/80 dark:text-amber-200/80">
          WorkMind transcribes it, extracts decisions and action items, and adds the transcript to the collection you choose.
        </p>
        <label className="flex items-center gap-2 text-xs font-medium">
          <Checkbox checked={consent} disabled={pending} onCheckedChange={(checked) => setConsent(checked === true)} />
          Everyone in this meeting knows
        </label>
        {showErrors && errors.consent ? <p className="text-xs text-destructive">{errors.consent}</p> : null}
      </div>

      {error ? <ErrorAlert error={error} /> : null}
      <Button type="submit" disabled={pending}>
        {pending ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : <Upload data-icon="inline-start" />}
        {pending ? "Uploading…" : "Upload"}
      </Button>
    </form>
  );
}
