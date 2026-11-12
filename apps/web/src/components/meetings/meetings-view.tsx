"use client";

import { CalendarClock, CircleAlert, FileAudio, FileText, LoaderCircle, ShieldCheck, Upload, X } from "lucide-react";
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
import { formatBytes, formatDateTime } from "@/lib/format";
import { knowledgeApi, knowledgeKeys, type Collection } from "@/lib/knowledge/api";
import { meetingsApi, meetingsKeys, type MeetingStatus, type MeetingSummary } from "@/lib/meetings/api";
import {
  ACCEPT,
  CONSENT_NOTICE,
  fileKind,
  formatDay,
  formatDuration,
  isProcessing,
  meetingErrorMessage,
  STATUS_LABELS,
  TITLE_MAX,
  titleAfterPick,
  uploadedText,
  validateUpload,
} from "@/lib/meetings/meetings";
import { toast } from "@/lib/stores/toasts";
import { cn } from "@/lib/utils";

const STATUS_STYLE: Record<MeetingStatus, string> = {
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
          A meeting follows its collection&apos;s access: whoever can view the collection sees it.
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
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
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
          <EmptyState
            icon={CalendarClock}
            title="No meetings yet"
            description="Upload a recording or a transcript to get its summary, decisions and action items. Meetings in collections you can view show up here too."
          />
        ) : (
          <ul className="flex flex-col divide-y rounded-xl border">
            {list.map((meeting) => (
              <MeetingRow key={meeting.id} meeting={meeting} collection={names.get(meeting.collection_id)} />
            ))}
          </ul>
        )}
      </section>
      <aside aria-label="Add a meeting">
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
      <Link
        href={`/meetings/${meeting.id}`}
        className="flex flex-col gap-1 px-4 py-3 hover:bg-muted/40 focus-visible:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none focus-visible:ring-inset"
      >
        <div className="flex items-start justify-between gap-3">
          <p className="min-w-0 font-medium break-words">{meeting.title}</p>
          <MeetingStatusBadge status={meeting.status} />
        </div>
        <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
          <span title={`Uploaded ${formatDateTime(meeting.created_at)}`}>{formatDay(meeting.held_on)}</span>
          {duration ? <span>{duration}</span> : null}
          {collection ? <span>{collection}</span> : null}
          {meeting.status === "ready" ? (
            <span>
              {meeting.decisions} {meeting.decisions === 1 ? "decision" : "decisions"} · {meeting.action_items}{" "}
              {meeting.action_items === 1 ? "action item" : "action items"}
            </span>
          ) : null}
        </p>
        {meeting.status === "failed" ? (
          <p className="flex items-start gap-1 text-xs text-destructive">
            <CircleAlert className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
            {meeting.error || "Processing failed."}
          </p>
        ) : null}
      </Link>
    </li>
  );
}

function UploadPanel({ collections }: { collections: Collection[] }) {
  const id = useId();
  const fileRef = useRef<HTMLInputElement>(null);
  const abort = useRef<AbortController | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [collection, setCollection] = useState(collections.length === 1 ? collections[0].key : "");
  const [consent, setConsent] = useState(false);
  const [showErrors, setShowErrors] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);
  const errors = validateUpload({ file, collection, consent });
  const kind = file ? fileKind(file.name) : null;
  const pending = progress !== null;
  const consentId = `${id}-consent`;
  const consentNoticeId = `${id}-consent-notice`;
  const consentErrorId = `${id}-consent-error`;

  if (collections.length === 0) {
    return (
      <div className="rounded-xl border p-4 text-sm text-muted-foreground">
        <p className="font-medium text-foreground">Add a meeting</p>
        <p className="mt-1">
          Meetings are stored in a collection, and you need edit access to one to add a meeting. Ask a collection&apos;s owner or
          an admin.
        </p>
      </div>
    );
  }

  async function upload() {
    setShowErrors(true);
    if (!file || Object.keys(errors).length > 0) return;
    const controller = new AbortController();
    abort.current = controller;
    setProgress(0);
    setError(null);
    try {
      const created = await meetingsApi.upload({ collection, filename: file.name, title: title.trim() || undefined }, file, {
        onProgress: setProgress,
        signal: controller.signal,
      });
      await invalidateQueries(meetingsKeys.list);
      toast({ title: `“${created.title}” uploaded`, description: uploadedText(kind) });
      setFile(null);
      setTitle("");
      setConsent(false);
      setShowErrors(false);
      if (fileRef.current) fileRef.current.value = "";
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) setError(err);
    } finally {
      abort.current = null;
      setProgress(null);
    }
  }

  return (
    <form
      className="flex flex-col gap-4 rounded-xl border bg-card p-4"
      noValidate
      aria-labelledby={`${id}-heading`}
      onSubmit={(e) => {
        e.preventDefault();
        void upload();
      }}
    >
      <div>
        <h3 id={`${id}-heading`} className="font-medium">
          Add a meeting
        </h3>
        <p className="text-xs text-muted-foreground">
          A recording (mp3, m4a, wav, mp4, webm, ogg, flac, aiff, mov) or a transcript (vtt, srt, txt, md), up to 500 MB.
        </p>
      </div>

      <Field label="File" error={showErrors ? (errors.file ?? null) : null}>
        {({ id: inputId, describedBy, invalid }) => (
          <Input
            id={inputId}
            ref={fileRef}
            type="file"
            accept={ACCEPT}
            disabled={pending}
            onChange={(e) => {
              const next = e.target.files?.[0] ?? null;
              setTitle((current) => titleAfterPick(current, file?.name ?? null, next?.name ?? null));
              setFile(next);
              setError(null);
            }}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
          />
        )}
      </Field>
      {file ? (
        <p className="-mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
          {kind === "audio" ? <FileAudio className="size-3.5" aria-hidden="true" /> : <FileText className="size-3.5" aria-hidden="true" />}
          {kind === "audio" ? "Recording" : kind === "transcript" ? "Transcript" : "Unsupported file"} · {formatBytes(file.size)}
        </p>
      ) : null}

      <Field label="Title" hint="Defaults to the file name.">
        {({ id: inputId, describedBy }) => (
          <Input
            id={inputId}
            maxLength={TITLE_MAX}
            value={title}
            disabled={pending}
            placeholder="Weekly operations sync"
            onChange={(e) => setTitle(e.target.value)}
            aria-describedby={describedBy}
          />
        )}
      </Field>

      <Field
        label="Collection"
        hint="Everyone who can view it sees the meeting and finds the transcript in search."
        error={showErrors ? (errors.collection ?? null) : null}
      >
        {({ id: selectId, describedBy, invalid }) => (
          <SimpleSelect
            id={selectId}
            describedBy={describedBy}
            invalid={invalid}
            value={collection}
            disabled={pending}
            onChange={setCollection}
            options={[{ value: "", label: "Choose a collection", disabled: true }, ...collections.map((c) => ({ value: c.key, label: c.name }))]}
          />
        )}
      </Field>

      <div
        className={cn(
          "flex flex-col gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm",
          showErrors && errors.consent && "border-destructive",
        )}
      >
        <p id={consentNoticeId} className="flex items-start gap-2 font-medium text-amber-900 dark:text-amber-200">
          <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          {CONSENT_NOTICE}
        </p>
        <p className="text-xs text-amber-900/80 dark:text-amber-200/80">
          WorkMind transcribes it on this server, sends the transcript to the configured language model to extract decisions and
          action items, and adds the transcript to the collection you choose.
        </p>
        <div className="flex items-center gap-2">
          <Checkbox
            id={consentId}
            checked={consent}
            disabled={pending}
            onCheckedChange={(checked) => setConsent(checked === true)}
            aria-describedby={[consentNoticeId, showErrors && errors.consent ? consentErrorId : null].filter(Boolean).join(" ")}
            aria-invalid={(showErrors && Boolean(errors.consent)) || undefined}
          />
          <label htmlFor={consentId} className="text-xs font-medium">
            Everyone in this meeting knows
          </label>
        </div>
        {showErrors && errors.consent ? (
          <p id={consentErrorId} className="text-xs text-destructive">
            {errors.consent}
          </p>
        ) : null}
      </div>

      {error ? <ErrorAlert error={error} message={meetingErrorMessage(error)} /> : null}

      {pending ? (
        <div className="flex flex-col gap-2">
          <div
            role="progressbar"
            aria-label="Upload progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round((progress ?? 0) * 100)}
            className="h-2 overflow-hidden rounded-full bg-muted"
          >
            <div className="h-full bg-primary transition-[width]" style={{ width: `${Math.round((progress ?? 0) * 100)}%` }} />
          </div>
          <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
              {progress !== null && progress >= 1 ? "Saving…" : `Uploading… ${Math.round((progress ?? 0) * 100)}%`}
            </span>
            <Button type="button" variant="ghost" size="xs" onClick={() => abort.current?.abort()}>
              <X data-icon="inline-start" aria-hidden="true" />
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <Button type="submit">
          <Upload data-icon="inline-start" aria-hidden="true" />
          Upload
        </Button>
      )}
    </form>
  );
}
