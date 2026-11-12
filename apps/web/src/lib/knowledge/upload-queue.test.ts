import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import { MAX_UPLOAD_BYTES, type KnowledgeDocument, type UploadResult } from "./api";
import { createUploadQueue, precheck, uploadErrorMessage, type FileLike, type Uploader } from "./upload-queue";

const doc = (name: string): KnowledgeDocument => ({
  id: `d-${name}`,
  collection_id: "c1",
  title: name,
  filename: name,
  mime_type: "text/markdown",
  trust: "official",
  sensitivity: "internal",
  status: "pending",
  error: null,
  chunk_count: 0,
  size_bytes: 10,
  version: 1,
  updated_at: "2026-11-04T06:00:00Z",
  owner: null,
  reviewed_at: null,
  reviewed_by: null,
  next_review_at: null,
  review_overdue: false,
  official_request: null,
});

const file = (name: string, size = 100): FileLike => ({ name, size });

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const problem = (status: number, code: string, title = "Problem") =>
  new ApiError({ status, code, title, problem: { type: "about:blank", title, status, code } });

/** Uploader whose calls are resolved by the test. */
function controllable() {
  const calls: { file: FileLike; done: ReturnType<typeof deferred<UploadResult>>; progress: (f: number) => void; signal: AbortSignal }[] = [];
  const upload: Uploader<FileLike> = (f, { onProgress, signal }) => {
    const done = deferred<UploadResult>();
    signal.addEventListener("abort", () => done.reject(new DOMException("aborted", "AbortError")));
    calls.push({ file: f, done, progress: onProgress, signal });
    return done.promise;
  };
  return { upload, calls };
}

const statusOf = (queue: ReturnType<typeof createUploadQueue<FileLike>>, name: string) =>
  queue.getSnapshot().find((i) => i.name === name);

describe("precheck", () => {
  it("accepts supported files", () => {
    for (const name of ["a.md", "B.PDF", "notes.txt", "deck.pptx", "sheet.xlsx", "x.docx", "page.html", "t.csv", "slack.json"]) {
      expect(precheck(file(name)), name).toBeNull();
    }
  });

  it("rejects unsupported types, oversized and empty files before uploading", () => {
    expect(precheck(file("photo.png"))?.code).toBe("unsupported_type");
    expect(precheck(file("README"))?.code).toBe("unsupported_type");
    expect(precheck(file("big.pdf", MAX_UPLOAD_BYTES + 1))?.code).toBe("too_large");
    expect(precheck(file("edge.pdf", MAX_UPLOAD_BYTES))).toBeNull();
    expect(precheck(file("empty.md", 0))?.code).toBe("empty_file");
  });
});

describe("uploadErrorMessage", () => {
  it.each([
    [problem(413, "too_large"), "File is too large"],
    [problem(415, "unsupported_type"), "File type not supported"],
    [problem(422, "empty_file"), "File is empty"],
    [problem(403, "forbidden"), "You can't upload here"],
    [problem(404, "not_found"), "Collection not found"],
    [new ApiError({ status: 0, title: "Network error", code: "network_error" }), "Can't reach the server"],
  ])("%#: maps to %s", (error, title) => {
    expect(uploadErrorMessage(error).title).toBe(title);
  });

  it("explains the 50 MB limit and the supported types", () => {
    expect(uploadErrorMessage(problem(413, "too_large")).description).toContain("50 MB");
    expect(uploadErrorMessage(problem(415, "unsupported_type")).description).toMatch(/PDF.*Slack export/);
  });
});

describe("upload queue", () => {
  it("goes queued → uploading (with progress) → uploaded, and reports the result", async () => {
    const { upload, calls } = controllable();
    const onUploaded = vi.fn();
    const queue = createUploadQueue({ upload, onUploaded });
    queue.add([file("policy.md")]);

    expect(statusOf(queue, "policy.md")).toMatchObject({ status: "uploading", progress: 0 });
    calls[0].progress(0.4);
    expect(statusOf(queue, "policy.md")?.progress).toBe(0.4);

    calls[0].done.resolve({ document: doc("policy.md"), unchanged: false });
    await vi.waitFor(() => expect(statusOf(queue, "policy.md")?.status).toBe("uploaded"));
    expect(statusOf(queue, "policy.md")).toMatchObject({ progress: 1, message: null, document: { id: "d-policy.md" } });
    expect(onUploaded).toHaveBeenCalledWith({ document: doc("policy.md"), unchanged: false });
  });

  it("marks identical re-uploads as unchanged with an explanation", async () => {
    const queue = createUploadQueue({ upload: async (f) => ({ document: doc(f.name), unchanged: true }) });
    queue.add([file("same.md")]);
    await vi.waitFor(() => expect(statusOf(queue, "same.md")?.status).toBe("unchanged"));
    expect(statusOf(queue, "same.md")?.message?.description).toMatch(/nothing was re-indexed/);
  });

  it("shows 413 and 415 from the server clearly and doesn't report them as uploaded", async () => {
    const onUploaded = vi.fn();
    const queue = createUploadQueue<FileLike>({
      upload: async (f) => {
        throw f.name === "huge.pdf" ? problem(413, "too_large") : problem(415, "unsupported_type");
      },
      onUploaded,
    });
    queue.add([file("huge.pdf"), file("weird.md")]);
    await vi.waitFor(() => expect(queue.getSnapshot().every((i) => i.status === "failed")).toBe(true));
    expect(statusOf(queue, "huge.pdf")?.message?.code).toBe("too_large");
    expect(statusOf(queue, "weird.md")?.message?.code).toBe("unsupported_type");
    expect(onUploaded).not.toHaveBeenCalled();
  });

  it("rejects bad files locally and still uploads the good ones", async () => {
    const upload = vi.fn(async (f: FileLike) => ({ document: doc(f.name), unchanged: false }));
    const queue = createUploadQueue({ upload });
    queue.add([file("ok.md"), file("logo.png"), file("big.pdf", MAX_UPLOAD_BYTES + 1)]);
    expect(statusOf(queue, "logo.png")).toMatchObject({ status: "rejected", message: { code: "unsupported_type" } });
    expect(statusOf(queue, "big.pdf")).toMatchObject({ status: "rejected", message: { code: "too_large" } });
    await vi.waitFor(() => expect(statusOf(queue, "ok.md")?.status).toBe("uploaded"));
    expect(upload).toHaveBeenCalledTimes(1);
  });

  it("uploads at most `concurrency` files at a time, in order", async () => {
    const { upload, calls } = controllable();
    const queue = createUploadQueue({ upload, concurrency: 2 });
    queue.add([file("1.md"), file("2.md"), file("3.md")]);
    expect(calls.map((c) => c.file.name)).toEqual(["1.md", "2.md"]);
    expect(statusOf(queue, "3.md")?.status).toBe("queued");

    calls[0].done.resolve({ document: doc("1.md"), unchanged: false });
    await vi.waitFor(() => expect(calls).toHaveLength(3));
    expect(calls[2].file.name).toBe("3.md");
  });

  it("cancels an upload in flight and lets the user retry it", async () => {
    const { upload, calls } = controllable();
    const queue = createUploadQueue({ upload });
    queue.add([file("a.md")]);
    const id = queue.getSnapshot()[0].id;

    queue.cancel(id);
    await vi.waitFor(() => expect(statusOf(queue, "a.md")?.status).toBe("cancelled"));
    expect(calls[0].signal.aborted).toBe(true);

    queue.retry(id);
    expect(statusOf(queue, "a.md")?.status).toBe("uploading");
    calls[1].done.resolve({ document: doc("a.md"), unchanged: false });
    await vi.waitFor(() => expect(statusOf(queue, "a.md")?.status).toBe("uploaded"));
  });

  it("cancels queued files without uploading them", () => {
    const { upload, calls } = controllable();
    const queue = createUploadQueue({ upload, concurrency: 1 });
    queue.add([file("1.md"), file("2.md")]);
    queue.cancel(queue.getSnapshot()[1].id);
    expect(statusOf(queue, "2.md")?.status).toBe("cancelled");
    expect(calls).toHaveLength(1);
  });

  it("retries a failed upload", async () => {
    let attempt = 0;
    const queue = createUploadQueue<FileLike>({
      upload: async (f) => {
        if (attempt++ === 0) throw new ApiError({ status: 0, title: "Network error", code: "network_error" });
        return { document: doc(f.name), unchanged: false };
      },
    });
    queue.add([file("flaky.md")]);
    await vi.waitFor(() => expect(statusOf(queue, "flaky.md")?.status).toBe("failed"));
    queue.retry(queue.getSnapshot()[0].id);
    await vi.waitFor(() => expect(statusOf(queue, "flaky.md")?.status).toBe("uploaded"));
  });

  it("clears finished items but keeps active ones", async () => {
    const { upload, calls } = controllable();
    const queue = createUploadQueue({ upload, concurrency: 1 });
    queue.add([file("done.md"), file("waiting.md"), file("bad.png")]);
    calls[0].done.resolve({ document: doc("done.md"), unchanged: false });
    await vi.waitFor(() => expect(statusOf(queue, "done.md")?.status).toBe("uploaded"));

    queue.clearFinished();
    expect(queue.getSnapshot().map((i) => i.name)).toEqual(["waiting.md"]);
    queue.dismiss(queue.getSnapshot()[0].id); // still uploading: not dismissable
    expect(queue.getSnapshot()).toHaveLength(1);
  });

  it("notifies subscribers and keeps a stable snapshot between changes", () => {
    const queue = createUploadQueue({ upload: () => new Promise<UploadResult>(() => {}) });
    const listener = vi.fn();
    queue.subscribe(listener);
    const before = queue.getSnapshot();
    expect(queue.getSnapshot()).toBe(before);
    queue.add([file("x.md")]);
    expect(listener).toHaveBeenCalled();
    expect(queue.getSnapshot()).not.toBe(before);
  });
});
