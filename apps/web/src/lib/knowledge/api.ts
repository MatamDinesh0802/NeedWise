/**
 * Typed client for the Knowledge API. Shapes mirror the Pydantic models in
 * apps/api/src/workmind/api/v1/knowledge.py.
 */

import { apiJson, uploadRaw, type UploadOptions } from "@/lib/api";

export type CollectionScope = "department" | "enterprise" | "restricted" | "personal";
export type Trust = "official" | "reference" | "discussion";
export type Sensitivity = "internal" | "confidential" | "restricted";
export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export interface Grant {
  principal: string;
  relation: "viewer" | "editor" | string;
}

export interface PersonRef {
  id: string;
  display_name: string;
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
  /** Approves official status and sees the collection's governance overview. */
  owner: PersonRef | null;
  /** Documents are due for review this many days after their last review; null: never. */
  review_interval_days: number | null;
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
  /** Archived by a document retention rule: kept, but out of search and answers. */
  archived_at?: string | null;
  /** Governance (docs/governance.md). */
  owner: PersonRef | null;
  reviewed_at: string | null;
  reviewed_by: PersonRef | null;
  next_review_at: string | null;
  review_overdue: boolean;
  /** How far an overdue official review escalated this cycle (docs/governance.md). */
  review_escalation?: "collection_owner" | "admins" | "demoted" | null;
  official_request: OfficialRequest | null;
}

/** A pending request to make a document official. */
export interface OfficialRequest {
  requested_by: PersonRef | null;
  requested_at: string;
  note: string | null;
  approvers: PersonRef[];
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
  /** The owner or a collection editor. */
  can_review: boolean;
  /** A collection editor or an org admin. */
  can_change_owner: boolean;
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
  owner: PersonRef | null;
  review_interval_days: number | null;
  /** Days an official review may be overdue before the collection's owner is told (1 to 365). */
  review_escalation_days: number;
  /** Days after the administrators were told before it is demoted to reference (null: never). */
  review_auto_demote_days: number | null;
}

/** Fields left out stay as they are; null clears them. */
export interface CollectionGovernanceUpdate {
  owner_id?: string | null;
  review_interval_days?: number | null;
  review_escalation_days?: number;
  review_auto_demote_days?: number | null;
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
  /** Brings back a document a retention rule archived (docs/enterprise.md). */
  unarchive: (documentId: string) =>
    apiJson<KnowledgeDocument>(`/api/v1/documents/${id(documentId)}/unarchive`, { method: "POST" }),
  deleteDocument: (documentId: string) => apiJson<void>(`/api/v1/documents/${id(documentId)}`, { method: "DELETE" }),
  /** Trust "official" needs `requestOfficial`; demoting from official takes a note. */
  updateLabels: (documentId: string, body: { trust?: Exclude<Trust, "official">; sensitivity?: Sensitivity; note?: string }) =>
    apiJson<KnowledgeDocument>(`/api/v1/documents/${id(documentId)}`, { method: "PATCH", json: body }),
  setOwner: (documentId: string, ownerId: string) =>
    apiJson<{ document_id: string; owner: PersonRef }>(`/api/v1/documents/${id(documentId)}/owner`, {
      method: "PUT",
      json: { owner_id: ownerId },
    }),
  markReviewed: (documentId: string, note?: string) =>
    apiJson<DocumentDetail>(`/api/v1/documents/${id(documentId)}/review`, {
      method: "POST",
      json: note?.trim() ? { note: note.trim() } : {},
    }),
  requestOfficial: (documentId: string, note: string) =>
    apiJson<DocumentDetail>(`/api/v1/documents/${id(documentId)}/official`, { method: "POST", json: { note } }),
  /** Plain link: the browser downloads with the session cookie (Content-Disposition: attachment). */
  fileUrl: (documentId: string) => `/api/v1/documents/${id(documentId)}/file`,

  // Administration (owner/admin). Admins manage sharing but can't read restricted documents.
  listAllCollections: () => apiJson<Items<AdminCollection>>("/api/v1/admin/collections"),
  createCollection: (body: CollectionCreate) =>
    apiJson<CollectionDetail>("/api/v1/admin/collections", { method: "POST", json: body }),
  replaceGrants: (collectionId: string, body: { viewers: string[]; editors: string[] }) =>
    apiJson<Items<Grant>>(`/api/v1/admin/collections/${id(collectionId)}/grants`, { method: "PUT", json: body }),
  updateGovernance: (collectionId: string, body: CollectionGovernanceUpdate) =>
    apiJson<AdminCollection>(`/api/v1/admin/collections/${id(collectionId)}`, { method: "PATCH", json: body }),
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

/** Uploads raw file bytes (not multipart) with upload progress (see `uploadRaw`). */
export function uploadDocument(collectionId: string, file: File, options: UploadOptions = {}): Promise<UploadResult> {
  const url = `/api/v1/collections/${id(collectionId)}/documents?filename=${encodeURIComponent(file.name)}`;
  return uploadRaw<UploadResult>(url, file, mimeTypeFor(file) ?? (file.type || "application/octet-stream"), options);
}
