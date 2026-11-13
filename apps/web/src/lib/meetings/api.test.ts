import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CSRF_HEADER, type XhrLike } from "@/lib/api";

import { meetingsApi } from "./api";

class FakeXhr implements XhrLike {
  method = "";
  url = "";
  headers: Record<string, string> = {};
  body: unknown = null;
  status = 0;
  responseText = "";
  upload: XhrLike["upload"] = { onprogress: null };
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
  send(body: Blob) {
    this.body = body;
  }
  abort() {
    this.onabort?.();
  }
  getResponseHeader() {
    return null;
  }
  respond(status: number, body: unknown) {
    this.status = status;
    this.responseText = JSON.stringify(body);
    this.onload?.();
  }
}

describe("meetingsApi.upload", () => {
  it("POSTs the raw file as application/octet-stream, with the collection, file name and title in the query", async () => {
    const xhr = new FakeXhr();
    const file = new File(["WEBVTT\n"], "weekly ops.vtt");
    const progress = vi.fn();
    const result = meetingsApi.upload({ collection: "operations-sops", filename: file.name, title: "Weekly ops" }, file, {
      onProgress: progress,
      createXhr: () => xhr,
    });

    expect(xhr.method).toBe("POST");
    expect(xhr.url).toBe("/api/v1/meetings?collection=operations-sops&filename=weekly+ops.vtt&title=Weekly+ops");
    expect(xhr.headers["Content-Type"]).toBe("application/octet-stream");
    expect(xhr.headers[CSRF_HEADER]).toBe("1");
    expect(xhr.body).toBe(file); // not multipart
    xhr.upload.onprogress?.({ loaded: 3, total: 4, lengthComputable: true });
    expect(progress).toHaveBeenCalledWith(0.75);

    xhr.respond(202, { id: "m1", status: "pending" });
    await expect(result).resolves.toEqual({ id: "m1", status: "pending" });
  });

  it("rejects with the API's problem (415) and can be cancelled", async () => {
    const xhr = new FakeXhr();
    const failed = meetingsApi.upload({ collection: "ops", filename: "deck.pdf" }, new Blob(["x"]), { createXhr: () => xhr });
    xhr.respond(415, { title: "Upload a recording or a transcript", status: 415, code: "unsupported_type" });
    await expect(failed).rejects.toMatchObject({ status: 415, code: "unsupported_type" });

    const slow = new FakeXhr();
    const controller = new AbortController();
    const cancelled = meetingsApi.upload({ collection: "ops", filename: "a.m4a" }, new Blob(["x"]), { signal: controller.signal, createXhr: () => slow });
    controller.abort();
    await expect(cancelled).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("meetingsApi", () => {
  const fetchMock = vi.fn<typeof fetch>();
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockImplementation(async () => new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  const call = (n: number) => {
    const [url, init] = fetchMock.mock.calls[n];
    return { url, method: init?.method, body: init?.body ? JSON.parse(String(init.body)) : undefined, csrf: new Headers(init?.headers).get(CSRF_HEADER) };
  };

  it("maps speakers, corrects an action item and makes a task, with CSRF", async () => {
    await meetingsApi.setSpeakers("m 1", { "Speaker 2": "u-wei", "Olivia Grant": null, SPEAKER_3: { name: "Jonas Brandt" } });
    await meetingsApi.editAction("m1", 2, { owner_user_id: "u-olivia", due: null });
    await meetingsApi.makeTask("m1", 0);

    expect(call(0)).toEqual({ url: "/api/v1/meetings/m%201/speakers", method: "PUT", body: { speakers: { "Speaker 2": "u-wei", "Olivia Grant": null, SPEAKER_3: { name: "Jonas Brandt" } } }, csrf: "1" });
    expect(call(1)).toEqual({ url: "/api/v1/meetings/m1/actions/2", method: "PATCH", body: { owner_user_id: "u-olivia", due: null }, csrf: "1" });
    expect(call(2)).toMatchObject({ url: "/api/v1/meetings/m1/actions/0/task", method: "POST", csrf: "1" });
  });

  it("reads the list, one meeting and my commitments; plays the recording from its own URL", async () => {
    await meetingsApi.list();
    await meetingsApi.get("m1");
    await meetingsApi.commitments();
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(["/api/v1/meetings", "/api/v1/meetings/m1", "/api/v1/me/commitments"]);
    expect(meetingsApi.mediaUrl("m/1")).toBe("/api/v1/meetings/m%2F1/media");
  });
});
