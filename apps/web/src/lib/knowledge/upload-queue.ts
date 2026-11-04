/**
 * Framework-free upload queue for one collection: validates files, uploads a few at a
 * time with progress, and records a clear outcome per file. Consumed via useSyncExternalStore.
 */

import { ApiError } from "@/lib/api";
import { errorMessage, type ErrorMessage } from "@/lib/error-messages";
import { extensionOf, MAX_UPLOAD_BYTES, SUPPORTED_TYPES, type KnowledgeDocument, type UploadResult } from "@/lib/knowledge/api";

export type UploadStatus =
  | "queued"
  | "uploading"
  /** Accepted; indexing runs in the background (status shows in the table). */
  | "uploaded"
  /** Identical to the current version; nothing was re-indexed. */
  | "unchanged"
  /** Rejected before upload (type, size, empty). */
  | "rejected"
  | "failed"
  | "cancelled";

export interface UploadItem {
  id: string;
  name: string;
  size: number;
  status: UploadStatus;
  progress: number;
  message: ErrorMessage | null;
  document: KnowledgeDocument | null;
}

export interface FileLike {
  name: string;
  size: number;
  type?: string;
}

export type Uploader<F extends FileLike> = (
  file: F,
  options: { onProgress: (fraction: number) => void; signal: AbortSignal },
) => Promise<UploadResult>;

const SUPPORTED_LIST = "PDF, Word, Excel, PowerPoint, Markdown, text, HTML, CSV and Slack export (JSON)";

export const UPLOAD_MESSAGES = {
  too_large: {
    title: "File is too large",
    description: `Files can be at most ${MAX_UPLOAD_BYTES / 1024 / 1024} MB.`,
    code: "too_large",
  },
  unsupported_type: {
    title: "File type not supported",
    description: `Supported: ${SUPPORTED_LIST}.`,
    code: "unsupported_type",
  },
  empty_file: { title: "File is empty", description: "There is nothing to index in this file.", code: "empty_file" },
  forbidden: {
    title: "You can't upload here",
    description: "You can view this collection but not add documents to it. Ask a collection editor or an administrator.",
    code: "forbidden",
  },
  not_found: {
    title: "Collection not found",
    description: "It may have been deleted, or you no longer have access.",
    code: "not_found",
  },
} satisfies Record<string, ErrorMessage>;

/** Client-side checks that mirror the API, so obvious problems never leave the browser. */
export function precheck(file: FileLike): ErrorMessage | null {
  if (!Object.hasOwn(SUPPORTED_TYPES, extensionOf(file.name))) return UPLOAD_MESSAGES.unsupported_type;
  if (file.size > MAX_UPLOAD_BYTES) return UPLOAD_MESSAGES.too_large;
  if (file.size === 0) return UPLOAD_MESSAGES.empty_file;
  return null;
}

/** Message for a failed upload, preferring upload-specific wording. */
export function uploadErrorMessage(error: unknown): ErrorMessage {
  if (error instanceof ApiError) {
    if (error.status === 413 || error.code === "too_large") return UPLOAD_MESSAGES.too_large;
    if (error.status === 415 || error.code === "unsupported_type") return UPLOAD_MESSAGES.unsupported_type;
    if (error.code === "empty_file") return UPLOAD_MESSAGES.empty_file;
    if (error.status === 403) return UPLOAD_MESSAGES.forbidden;
    if (error.status === 404) return UPLOAD_MESSAGES.not_found;
  }
  return errorMessage(error);
}

export const FINISHED: ReadonlySet<UploadStatus> = new Set(["uploaded", "unchanged", "rejected", "failed", "cancelled"]);

export interface UploadQueue<F extends FileLike> {
  getSnapshot: () => readonly UploadItem[];
  subscribe: (listener: () => void) => () => void;
  add: (files: Iterable<F>) => void;
  retry: (id: string) => void;
  cancel: (id: string) => void;
  dismiss: (id: string) => void;
  clearFinished: () => void;
}

export function createUploadQueue<F extends FileLike>({
  upload,
  concurrency = 2,
  onUploaded,
}: {
  upload: Uploader<F>;
  concurrency?: number;
  /** Called when the server accepted a file (new or unchanged), e.g. to refresh the list. */
  onUploaded?: (result: UploadResult) => void;
}): UploadQueue<F> {
  let items: readonly UploadItem[] = [];
  const files = new Map<string, F>();
  const controllers = new Map<string, AbortController>();
  const listeners = new Set<() => void>();
  let counter = 0;

  const emit = () => {
    for (const listener of listeners) listener();
  };
  const patch = (id: string, change: Partial<UploadItem>) => {
    items = items.map((item) => (item.id === id ? { ...item, ...change } : item));
    emit();
  };
  const find = (id: string) => items.find((item) => item.id === id);

  async function run(id: string) {
    const file = files.get(id);
    if (!file) return;
    const controller = new AbortController();
    controllers.set(id, controller);
    patch(id, { status: "uploading", progress: 0, message: null });
    try {
      const result = await upload(file, {
        signal: controller.signal,
        onProgress: (fraction) => {
          if (find(id)?.status === "uploading") patch(id, { progress: fraction });
        },
      });
      patch(id, {
        status: result.unchanged ? "unchanged" : "uploaded",
        progress: 1,
        document: result.document,
        message: result.unchanged
          ? {
              title: "Already up to date",
              description: "This file is identical to the current version, so nothing was re-indexed.",
              code: null,
            }
          : null,
      });
      onUploaded?.(result);
    } catch (error) {
      if (controller.signal.aborted) patch(id, { status: "cancelled", message: null });
      else patch(id, { status: "failed", message: uploadErrorMessage(error) });
    } finally {
      controllers.delete(id);
      pump();
    }
  }

  function pump() {
    let active = items.filter((item) => item.status === "uploading").length;
    for (const item of items) {
      if (active >= concurrency) break;
      if (item.status === "queued" && !controllers.has(item.id)) {
        active++;
        void run(item.id);
      }
    }
  }

  return {
    getSnapshot: () => items,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    add(newFiles) {
      const added: UploadItem[] = [];
      for (const file of newFiles) {
        const id = `upload-${++counter}`;
        const problem = precheck(file);
        files.set(id, file);
        added.push({
          id,
          name: file.name,
          size: file.size,
          status: problem ? "rejected" : "queued",
          progress: 0,
          message: problem,
          document: null,
        });
      }
      if (added.length === 0) return;
      items = [...items, ...added];
      emit();
      pump();
    },
    retry(id) {
      const item = find(id);
      if (!item || (item.status !== "failed" && item.status !== "cancelled")) return;
      patch(id, { status: "queued", progress: 0, message: null });
      pump();
    },
    cancel(id) {
      const item = find(id);
      if (!item) return;
      if (item.status === "uploading") controllers.get(id)?.abort();
      else if (item.status === "queued") patch(id, { status: "cancelled" });
    },
    dismiss(id) {
      const item = find(id);
      if (!item || !FINISHED.has(item.status)) return;
      files.delete(id);
      items = items.filter((i) => i.id !== id);
      emit();
    },
    clearFinished() {
      const remaining = items.filter((item) => !FINISHED.has(item.status));
      if (remaining.length === items.length) return;
      for (const item of items) if (FINISHED.has(item.status)) files.delete(item.id);
      items = remaining;
      emit();
    },
  };
}
