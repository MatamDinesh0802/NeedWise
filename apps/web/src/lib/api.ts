/**
 * The one way the web app talks to the API.
 *
 * - Same-origin only: Caddy serves the web app and `/api` together; the session
 *   lives in an HttpOnly cookie that the browser attaches automatically.
 * - Every state-changing request carries the CSRF header the API requires.
 * - Error responses (RFC 9457 problem+json) become a typed `ApiError`.
 */

export const CSRF_HEADER = "X-CSRF-Protection";
const STATE_CHANGING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/** RFC 9457 problem details, plus the API's machine-readable `code`. */
export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  code?: string;
  detail?: string;
  [extension: string]: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  /** Machine-readable error code from the API (e.g. "unauthenticated", "csrf_header"). */
  readonly code: string | null;
  readonly title: string;
  readonly detail: string | null;
  readonly problem: ProblemDetails | null;
  readonly requestId: string | null;

  constructor(init: {
    status: number;
    title: string;
    code?: string | null;
    detail?: string | null;
    problem?: ProblemDetails | null;
    requestId?: string | null;
  }) {
    super(init.detail ? `${init.title}: ${init.detail}` : init.title);
    this.name = "ApiError";
    this.status = init.status;
    this.title = init.title;
    this.code = init.code ?? null;
    this.detail = init.detail ?? null;
    this.problem = init.problem ?? null;
    this.requestId = init.requestId ?? null;
  }

  get isUnauthenticated(): boolean {
    return this.status === 401;
  }
}

export interface ApiRequestInit extends Omit<RequestInit, "body"> {
  body?: BodyInit | null;
  /** Serialized as JSON with the matching Content-Type. Mutually exclusive with `body`. */
  json?: unknown;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Returns the problem document if `body` looks like one, otherwise null. */
export function parseProblem(body: unknown, fallbackStatus: number): ProblemDetails | null {
  if (!isRecord(body) || typeof body.title !== "string") return null;
  return {
    ...body,
    type: typeof body.type === "string" ? body.type : "about:blank",
    title: body.title,
    status: typeof body.status === "number" ? body.status : fallbackStatus,
    code: typeof body.code === "string" ? body.code : undefined,
    detail: typeof body.detail === "string" ? body.detail : undefined,
  };
}

async function errorFromResponse(response: Response): Promise<ApiError> {
  const requestId = response.headers.get("X-Request-Id");
  const contentType = response.headers.get("Content-Type") ?? "";
  let problem: ProblemDetails | null = null;
  if (contentType.includes("json")) {
    try {
      problem = parseProblem(await response.json(), response.status);
    } catch {
      problem = null; // Malformed JSON: fall back to the status line.
    }
  }
  return new ApiError({
    status: response.status,
    title: problem?.title ?? (response.statusText || `HTTP ${response.status}`),
    code: problem?.code ?? null,
    detail: problem?.detail ?? null,
    problem,
    requestId,
  });
}

/** Builds the final RequestInit: CSRF header, JSON body, same-origin credentials. */
export function buildRequestInit(init: ApiRequestInit = {}): RequestInit {
  const { json, headers: rawHeaders, ...rest } = init;
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(rawHeaders);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  if (STATE_CHANGING_METHODS.has(method)) headers.set(CSRF_HEADER, "1");

  let body = rest.body;
  if (json !== undefined) {
    if (body != null) throw new Error("Pass either `json` or `body`, not both");
    body = JSON.stringify(json);
    if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  }

  return {
    cache: "no-store",
    credentials: "same-origin",
    ...rest,
    method,
    headers,
    body,
  };
}

/** Fetches an API path. Resolves only for 2xx responses; throws `ApiError` otherwise. */
export async function apiFetch(path: string, init: ApiRequestInit = {}): Promise<Response> {
  if (!path.startsWith("/api/")) {
    throw new Error(`apiFetch only calls same-origin API paths, got "${path}"`);
  }
  let response: Response;
  try {
    response = await fetch(path, buildRequestInit(init));
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError({
      status: 0,
      title: "Network error",
      code: "network_error",
      detail: error instanceof Error ? error.message : null,
    });
  }
  if (!response.ok) throw await errorFromResponse(response);
  return response;
}

/** `apiFetch` + JSON decoding. Returns `undefined` for empty bodies (204, or 202 from /cancel). */
export async function apiJson<T>(path: string, init: ApiRequestInit = {}): Promise<T> {
  const response = await apiFetch(path, init);
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** The parts of XMLHttpRequest an upload uses (injected in tests). */
export interface XhrLike {
  open(method: string, url: string): void;
  setRequestHeader(name: string, value: string): void;
  send(body: Blob): void;
  abort(): void;
  status: number;
  responseText: string;
  getResponseHeader(name: string): string | null;
  upload: { onprogress: ((event: { loaded: number; total: number; lengthComputable: boolean }) => void) | null };
  onload: (() => void) | null;
  onerror: (() => void) | null;
  onabort: (() => void) | null;
}

export interface UploadOptions {
  /** 0…1 as the bytes go out. */
  onProgress?: (fraction: number) => void;
  signal?: AbortSignal;
  createXhr?: () => XhrLike;
}

/**
 * POSTs a file's raw bytes (not multipart) to an API path, with upload progress. fetch() can't
 * report upload progress, so this uses XMLHttpRequest. Errors become ApiError like apiFetch's;
 * cancelling through `signal` rejects with an AbortError.
 */
export function uploadRaw<T>(
  path: string,
  body: Blob,
  contentType: string,
  { onProgress, signal, createXhr = () => new XMLHttpRequest() as unknown as XhrLike }: UploadOptions = {},
): Promise<T> {
  if (!path.startsWith("/api/")) throw new Error(`uploadRaw only calls same-origin API paths, got "${path}"`);
  return new Promise((resolve, reject) => {
    const xhr = createXhr();
    xhr.open("POST", path);
    xhr.setRequestHeader(CSRF_HEADER, "1");
    xhr.setRequestHeader("Accept", "application/json");
    xhr.setRequestHeader("Content-Type", contentType);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress?.(Math.min(1, event.loaded / event.total));
    };
    xhr.onload = () => {
      let parsed: unknown = null;
      try {
        parsed = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        parsed = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(parsed as T);
        return;
      }
      const problem = parseProblem(parsed, xhr.status);
      reject(
        new ApiError({
          status: xhr.status,
          title: problem?.title ?? `HTTP ${xhr.status}`,
          code: problem?.code ?? null,
          detail: problem?.detail ?? null,
          problem,
          requestId: xhr.getResponseHeader("X-Request-Id"),
        }),
      );
    };
    xhr.onerror = () =>
      reject(new ApiError({ status: 0, title: "Network error", code: "network_error", detail: "The upload was interrupted." }));
    xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
    if (signal?.aborted) {
      reject(new DOMException("Upload cancelled", "AbortError"));
      return;
    }
    signal?.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(body);
  });
}
