/**
 * Typed client for the Knowledge API. Shapes mirror the Pydantic models in
 * apps/api/src/workmind/api/v1/knowledge.py.
 */

import { apiJson, ApiError, CSRF_HEADER, parseProblem } from "@/lib/api";

export type CollectionScope = "department" | "enterprise" | "restricted" | "personal";
export type Trust = "official" | "reference" | "discussion";
export type Sensitivity = "internal" | "confidential" | "restricted";
export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export interface Grant {
  principal: string;
  relation: "viewer" | "editor" | string;
}

export interface Collection {
  id: string;
  key: string;
  name: string;
  description: string;
  scope: CollectionScope;
  default_trust: Trust;
  default_sensitivity: Sensitivity;
  document_count: number;
  can_edit: boolean;
}

export interface CollectionDetail extends Collection {
  /** Only filled in for editors. */
  grants: Grant[];
}

export interface KnowledgeDocument {
  id: string;
  collection_id: string;
  title: string;
  filename: string;
  mime_type: string;
  trust: Trust;
  sensitivity: Sensitivity;
  status: DocumentStatus;
  error: string | null;
  chunk_count: number;
  size_bytes: number;
  version: number;
  updated_at: string;
}

export interface DocumentVersion {
  id: string;
  version_no: number;
  content_hash: string;
  size_bytes: number;
  chunk_count: number;
  embedding_model: string | null;
  created_at: string;
  processed_at: string | null;
}

export interface DocumentDetail extends KnowledgeDocument {
  versions: DocumentVersion[];
  can_edit: boolean;
}

export interface Chunk {
  id: string;
  ordinal: number;
  heading_path: string[];
  page: number | null;
  token_count: number;
  text: string;
}

export interface UploadResult {
  document: KnowledgeDocument;
  /** Identical bytes to the current version: nothing was re-indexed. */
  unchanged: boolean;
}

export interface AdminCollection {
  id: string;
  key: string;
  name: string;
  description: string;
  scope: CollectionScope;
  department_id: string | null;
  default_trust: Trust;
  default_sensitivity: Sensitivity;
  document_count: number;
  grants: Grant[];
}

export interface CollectionCreate {
  key: string;
  name: string;
  description: string;
  scope: Exclude<CollectionScope, "personal">;
  department_id?: string | null;
  default_trust: Trust;
  default_sensitivity: Sensitivity;
  viewers: string[];
  editors: string[];
}

interface Items<T> {
  items: T[];
}

const id = (value: string) => encodeURIComponent(value);

export const knowledgeApi = {
  listCollections: () => apiJson<Items<Collection>>("/api/v1/collections"),
  getCollection: (collectionId: string) => apiJson<CollectionDetail>(`/api/v1/collections/${id(collectionId)}`),
  listDocuments: (collectionId: string) =>
    apiJson<Items<KnowledgeDocument>>(`/api/v1/collections/${id(collectionId)}/documents`),
  getDocument: (documentId: string) => apiJson<DocumentDetail>(`/api/v1/documents/${id(documentId)}`),
  listChunks: (documentId: string) => apiJson<Items<Chunk>>(`/api/v1/documents/${id(documentId)}/chunks`),
  reindex: (documentId: string) =>
    apiJson<KnowledgeDocument>(`/api/v1/documents/${id(documentId)}/reindex`, { method: "POST" }),
  deleteDocument: (documentId: string) => apiJson<void>(`/api/v1/documents/${id(documentId)}`, { method: "DELETE" }),
  /** Plain link: the browser downloads with the session cookie (Content-Disposition: attachment). */
  fileUrl: (documentId: string) => `/api/v1/documents/${id(documentId)}/file`,

  // Administration (owner/admin). Admins manage sharing but can't read restricted documents.
  listAllCollections: () => apiJson<Items<AdminCollection>>("/api/v1/admin/collections"),
  createCollection: (body: CollectionCreate) =>
    apiJson<CollectionDetail>("/api/v1/admin/collections", { method: "POST", json: body }),
  replaceGrants: (collectionId: string, body: { viewers: string[]; editors: string[] }) =>
    apiJson<Items<Grant>>(`/api/v1/admin/collections/${id(collectionId)}/grants`, { method: "PUT", json: body }),
};

export const knowledgeKeys = {
  all: "knowledge/",
  collections: "knowledge/collections",
  collection: (collectionId: string) => `knowledge/collections/${collectionId}`,
  documents: (collectionId: string) => `knowledge/collections/${collectionId}/documents`,
  document: (documentId: string) => `knowledge/documents/${documentId}`,
  chunks: (documentId: string) => `knowledge/documents/${documentId}/chunks`,
  adminCollections: "admin/collections",
};

// --- Upload -------------------------------------------------------------------------------

/** Mirrors MIME_TYPES in apps/api/src/workmind/modules/knowledge/parsing.py. */
export const SUPPORTED_TYPES: Record<string, string> = {
  ".md": "text/markdown",
  ".markdown": "text/markdown",
  ".txt": "text/plain",
  ".html": "text/html",
  ".htm": "text/html",
  ".csv": "text/csv",
  ".json": "application/json",
  ".pdf": "application/pdf",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
};

export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

export function extensionOf(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot <= 0 ? "" : filename.slice(dot).toLowerCase();
}

export function mimeTypeFor(file: { name: string; type?: string }): string | null {
  return SUPPORTED_TYPES[extensionOf(file.name)] ?? null;
}

/** For the file input's `accept` attribute. */
export const ACCEPT = Object.keys(SUPPORTED_TYPES).join(",");

interface XhrLike {
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

/**
 * Uploads raw file bytes (not multipart) with upload progress. fetch() can't report
 * upload progress, so this uses XMLHttpRequest. Errors become ApiError like apiFetch's.
 */
export function uploadDocument(
  collectionId: string,
  file: File,
  {
    onProgress,
    signal,
    createXhr = () => new XMLHttpRequest() as unknown as XhrLike,
  }: { onProgress?: (fraction: number) => void; signal?: AbortSignal; createXhr?: () => XhrLike } = {},
): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const xhr = createXhr();
    const url = `/api/v1/collections/${id(collectionId)}/documents?filename=${encodeURIComponent(file.name)}`;
    xhr.open("POST", url);
    xhr.setRequestHeader(CSRF_HEADER, "1");
    xhr.setRequestHeader("Accept", "application/json");
    xhr.setRequestHeader("Content-Type", mimeTypeFor(file) ?? (file.type || "application/octet-stream"));
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress?.(Math.min(1, event.loaded / event.total));
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        body = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as UploadResult);
        return;
      }
      const problem = parseProblem(body, xhr.status);
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
    signal?.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(file);
  });
}
