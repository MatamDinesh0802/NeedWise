"use client";

import { CircleAlert, CircleCheck, CloudUpload, Info, LoaderCircle, RotateCw, X } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";

import { Button } from "@/components/ui/button";
import { useStore } from "@/hooks/use-store";
import { ACCEPT, MAX_UPLOAD_BYTES } from "@/lib/knowledge/api";
import { FINISHED, type UploadItem, type UploadQueue } from "@/lib/knowledge/upload-queue";
import { formatBytes } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Drop zone + file picker (several files at once) and a per-file progress list. */
export function UploadPanel({ queue }: { queue: UploadQueue<File> }) {
  const items = useStore(queue);
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    if (event.dataTransfer.files.length) queue.add(Array.from(event.dataTransfer.files));
  }

  const finished = items.filter((i) => FINISHED.has(i.status)).length;

  return (
    <section aria-label="Upload documents" className="flex flex-col gap-3">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          "flex flex-col items-center gap-2 rounded-xl border border-dashed px-6 py-6 text-center transition-colors",
          dragging ? "border-primary bg-primary/5" : "bg-muted/20",
        )}
      >
        <CloudUpload className="size-6 text-muted-foreground" aria-hidden="true" />
        <p className="text-sm">
          <span className="font-medium">Drop files here</span> or{" "}
          <Button variant="link" className="h-auto p-0" onClick={() => input.current?.click()}>
            choose files
          </Button>
        </p>
        <p className="text-xs text-muted-foreground">
          PDF, Word, Excel, PowerPoint, Markdown, text, HTML, CSV or a Slack export (JSON) · up to{" "}
          {formatBytes(MAX_UPLOAD_BYTES)} each. A file with the same name becomes a new version.
        </p>
        <input
          ref={input}
          type="file"
          multiple
          accept={ACCEPT}
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(e) => {
            if (e.target.files?.length) queue.add(Array.from(e.target.files));
            e.target.value = ""; // allow choosing the same file again
          }}
        />
      </div>

      {items.length > 0 ? (
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-medium">Uploads</h4>
            {finished > 0 ? (
              <Button variant="ghost" size="sm" onClick={queue.clearFinished}>
                Clear finished
              </Button>
            ) : null}
          </div>
          <ul className="flex flex-col divide-y rounded-lg border" aria-live="polite">
            {items.map((item) => (
              <UploadRow key={item.id} item={item} queue={queue} />
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function UploadRow({ item, queue }: { item: UploadItem; queue: UploadQueue<File> }) {
  const percent = Math.round(item.progress * 100);
  const bad = item.status === "failed" || item.status === "rejected";
  return (
    <li className="flex flex-col gap-1.5 px-3 py-2.5">
      <div className="flex items-center gap-2">
        <StatusIcon item={item} />
        <span className="min-w-0 flex-1 truncate text-sm font-medium" title={item.name}>
          {item.name}
        </span>
        <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{formatBytes(item.size)}</span>
        {item.status === "failed" || item.status === "cancelled" ? (
          <Button variant="ghost" size="icon-xs" aria-label={`Retry ${item.name}`} onClick={() => queue.retry(item.id)}>
            <RotateCw />
          </Button>
        ) : null}
        {FINISHED.has(item.status) ? (
          <Button variant="ghost" size="icon-xs" aria-label={`Dismiss ${item.name}`} onClick={() => queue.dismiss(item.id)}>
            <X />
          </Button>
        ) : (
          <Button variant="ghost" size="icon-xs" aria-label={`Cancel ${item.name}`} onClick={() => queue.cancel(item.id)}>
            <X />
          </Button>
        )}
      </div>
      {item.status === "uploading" ? (
        <div
          className="h-1.5 overflow-hidden rounded-full bg-muted"
          role="progressbar"
          aria-label={`Uploading ${item.name}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
        >
          <div className="h-full bg-primary transition-[width]" style={{ width: `${percent}%` }} />
        </div>
      ) : null}
      <p className={cn("pl-6 text-xs", bad ? "text-destructive" : "text-muted-foreground")}>
        {describe(item)}
      </p>
    </li>
  );
}

function describe(item: UploadItem): string {
  switch (item.status) {
    case "queued":
      return "Waiting…";
    case "uploading":
      return `Uploading… ${Math.round(item.progress * 100)}%`;
    case "uploaded":
      return "Uploaded. Indexing runs in the background; follow its status in the table below.";
    case "unchanged":
    case "rejected":
    case "failed":
      return item.message ? `${item.message.title}. ${item.message.description}` : "";
    case "cancelled":
      return "Cancelled.";
  }
}

function StatusIcon({ item }: { item: UploadItem }) {
  const className = "size-4 shrink-0";
  switch (item.status) {
    case "uploading":
    case "queued":
      return <LoaderCircle className={cn(className, "animate-spin text-muted-foreground")} aria-hidden="true" />;
    case "uploaded":
      return <CircleCheck className={cn(className, "text-emerald-600 dark:text-emerald-400")} aria-hidden="true" />;
    case "unchanged":
      return <Info className={cn(className, "text-sky-600 dark:text-sky-400")} aria-hidden="true" />;
    default:
      return <CircleAlert className={cn(className, item.status === "cancelled" ? "text-muted-foreground" : "text-destructive")} aria-hidden="true" />;
  }
}
