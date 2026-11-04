import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, CSRF_HEADER } from "@/lib/api";

import { ACCEPT, extensionOf, knowledgeApi, mimeTypeFor, uploadDocument } from "./api";

class FakeXhr {
  method = "";
  url = "";
  headers: Record<string, string> = {};
  body: unknown = null;
  status = 0;
  responseText = "";
  responseHeaders: Record<string, string> = {};
  upload = { onprogress: null as ((e: { loaded: number; total: number; lengthComputable: boolean }) => void) | null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }
  send(body: unknown) {
    this.body = body;
  }
  abort() {
    this.onabort?.();
  }
  getResponseHeader(name: string) {
    return this.responseHeaders[name] ?? null;
  }
  respond(status: number, body: unknown) {
    this.status = status;
    this.responseText = JSON.stringify(body);
    this.onload?.();
  }
}

const mdFile = new File(["# Hello\n"], "Parental Leave (2026).md", { type: "" });

describe("uploadDocument", () => {
  it("POSTs the raw bytes with CSRF, the right Content-Type and the filename", async () => {
    const xhr = new FakeXhr();
    const progress = vi.fn();
    const result = uploadDocument("c 1", mdFile, { onProgress: progress, createXhr: () => xhr });

    expect(xhr.method).toBe("POST");
    expect(xhr.url).toBe("/api/v1/collections/c%201/documents?filename=Parental%20Leave%20(2026).md");
    expect(xhr.headers[CSRF_HEADER]).toBe("1");
    expect(xhr.headers["Content-Type"]).toBe("text/markdown"); // browser gave "", we map by extension
    expect(xhr.body).toBe(mdFile); // not multipart

    xhr.upload.onprogress?.({ loaded: 5, total: 10, lengthComputable: true });
    expect(progress).toHaveBeenCalledWith(0.5);

    xhr.respond(202, { document: { id: "d1" }, unchanged: true });
    await expect(result).resolves.toEqual({ document: { id: "d1" }, unchanged: true });
  });

  it("turns problem+json errors into ApiError (413 too_large)", async () => {
    const xhr = new FakeXhr();
    xhr.responseHeaders["X-Request-Id"] = "req-9";
    const result = uploadDocument("c1", mdFile, { createXhr: () => xhr });
    xhr.respond(413, { type: "about:blank", title: "The file is too large", status: 413, code: "too_large" });
    const error = await result.catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 413, code: "too_large", requestId: "req-9" });
  });

  it("reports network failures and cancellation", async () => {
    const failing = new FakeXhr();
    const failed = uploadDocument("c1", mdFile, { createXhr: () => failing });
    failing.onerror?.();
    await expect(failed).rejects.toMatchObject({ status: 0, code: "network_error" });

    const xhr = new FakeXhr();
    const controller = new AbortController();
    const cancelled = uploadDocument("c1", mdFile, { signal: controller.signal, createXhr: () => xhr });
    controller.abort();
    await expect(cancelled).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("file types", () => {
  it("maps extensions like the API does", () => {
    expect(extensionOf("Report.FINAL.PDF")).toBe(".pdf");
    expect(extensionOf(".bashrc")).toBe("");
    expect(extensionOf("noext")).toBe("");
    expect(mimeTypeFor({ name: "slack-export.json" })).toBe("application/json");
    expect(mimeTypeFor({ name: "deck.pptx" })).toBe(
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    );
    expect(mimeTypeFor({ name: "photo.png" })).toBeNull();
    expect(ACCEPT.split(",")).toEqual(expect.arrayContaining([".pdf", ".md", ".json", ".xlsx"]));
  });
});

describe("knowledgeApi", () => {
  const fetchMock = vi.fn<typeof fetch>();
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockImplementation(async () => new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  const last = () => {
    const [url, init] = fetchMock.mock.calls.at(-1)!;
    return { url, method: init?.method, headers: new Headers(init?.headers), body: init?.body };
  };

  it("uses the documented endpoints and CSRF for changes", async () => {
    await knowledgeApi.reindex("d1");
    expect(last()).toMatchObject({ url: "/api/v1/documents/d1/reindex", method: "POST" });
    expect(last().headers.get(CSRF_HEADER)).toBe("1");

    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await knowledgeApi.deleteDocument("d1");
    expect(last()).toMatchObject({ url: "/api/v1/documents/d1", method: "DELETE" });

    await knowledgeApi.replaceGrants("c1", { viewers: ["group:x"], editors: [] });
    expect(last()).toMatchObject({ url: "/api/v1/admin/collections/c1/grants", method: "PUT" });
    expect(JSON.parse(String(last().body))).toEqual({ viewers: ["group:x"], editors: [] });

    await knowledgeApi.listChunks("d1");
    expect(last()).toMatchObject({ url: "/api/v1/documents/d1/chunks", method: "GET" });
    expect(last().headers.has(CSRF_HEADER)).toBe(false);
    expect(knowledgeApi.fileUrl("d 1")).toBe("/api/v1/documents/d%201/file");
  });
});
