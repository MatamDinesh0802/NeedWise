/** Display labels and explanations for knowledge metadata. */

import type { CollectionScope, DocumentStatus, Sensitivity, Trust } from "@/lib/knowledge/api";
import { SCOPE_DESCRIPTIONS } from "@/lib/knowledge/principals";

export const SCOPE_LABELS: Record<CollectionScope, string> = {
  enterprise: "Enterprise",
  department: "Department",
  restricted: "Restricted",
  personal: "Personal",
};

export { SCOPE_DESCRIPTIONS };

export const TRUST_LABELS: Record<Trust, { label: string; description: string }> = {
  official: { label: "Official", description: "Approved policy. Wins over other sources when they disagree." },
  reference: { label: "Reference", description: "Normal documentation, not formally approved." },
  discussion: {
    label: "Discussion",
    description: "Chats and conversations. Not authoritative; answers label it as discussion.",
  },
};

/** Sensitivity says who *should* see a document; the collection's sharing decides who *can*. */
export const SENSITIVITY_LABELS: Record<Sensitivity, { label: string; description: string }> = {
  internal: { label: "Internal", description: "Fine for anyone who can open the collection." },
  confidential: {
    label: "Confidential",
    description: "Business-sensitive: should only reach people who need it. Masked in logs and traces.",
  },
  restricted: {
    label: "Restricted",
    description:
      "Highly sensitive (personal data, salaries, legal): should only be seen by named people. Who can open it is set by the collection's sharing.",
  },
};

export const STATUS_LABELS: Record<DocumentStatus, { label: string; description: string }> = {
  pending: { label: "Queued", description: "Waiting to be processed." },
  processing: { label: "Processing", description: "Being parsed, chunked and indexed." },
  ready: { label: "Ready", description: "Indexed and searchable." },
  failed: { label: "Failed", description: "Processing failed. See the error, then re-index or upload again." },
};

/** "Markdown", "PDF", … from a MIME type. */
export function fileTypeLabel(mime: string): string {
  const map: Record<string, string> = {
    "text/markdown": "Markdown",
    "text/plain": "Text",
    "text/html": "HTML",
    "text/csv": "CSV",
    "application/json": "Slack export (JSON)",
    "application/pdf": "PDF",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "Excel",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PowerPoint",
  };
  return map[mime] ?? mime;
}

/** "2. Parental leave > 2.1 Duration"; empty path → null. */
export function headingTrail(path: readonly string[]): string | null {
  const parts = path.map((p) => p.trim()).filter(Boolean);
  return parts.length ? parts.join(" > ") : null;
}
