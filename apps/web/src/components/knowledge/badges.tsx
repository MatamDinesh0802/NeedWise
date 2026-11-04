"use client";

import {
  CircleCheck,
  CircleDashed,
  CircleX,
  File,
  FileBraces,
  FileCode,
  FileSpreadsheet,
  FileText,
  LoaderCircle,
  Lock,
  Presentation,
  type LucideIcon,
} from "lucide-react";
import { createElement, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { CollectionScope, DocumentStatus, Sensitivity, Trust } from "@/lib/knowledge/api";
import {
  fileTypeLabel,
  SCOPE_DESCRIPTIONS,
  SCOPE_LABELS,
  SENSITIVITY_LABELS,
  STATUS_LABELS,
  TRUST_LABELS,
} from "@/lib/knowledge/labels";
import { cn } from "@/lib/utils";

/** A badge with an explanation on hover and keyboard focus. */
function Explained({ tip, children }: { tip: string; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger render={<span tabIndex={0} className="inline-flex rounded-4xl outline-none focus-visible:ring-2 focus-visible:ring-ring/50" />}>
        {children}
      </TooltipTrigger>
      <TooltipContent>{tip}</TooltipContent>
    </Tooltip>
  );
}

export function ScopeBadge({ scope }: { scope: CollectionScope }) {
  return (
    <Explained tip={SCOPE_DESCRIPTIONS[scope]}>
      <Badge
        variant={scope === "restricted" ? "default" : "secondary"}
        className={cn(scope === "restricted" && "bg-amber-600 text-white dark:bg-amber-500 dark:text-black")}
      >
        {scope === "restricted" ? <Lock data-icon="inline-start" aria-hidden="true" /> : null}
        {SCOPE_LABELS[scope] ?? scope}
      </Badge>
    </Explained>
  );
}

const TRUST_STYLE: Record<Trust, string> = {
  official: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  reference: "bg-sky-500/10 text-sky-700 dark:text-sky-400",
  discussion: "bg-violet-500/10 text-violet-700 dark:text-violet-400",
};

export function TrustBadge({ trust }: { trust: Trust }) {
  const info = TRUST_LABELS[trust] ?? { label: trust, description: "" };
  return (
    <Explained tip={info.description}>
      <Badge variant="secondary" className={TRUST_STYLE[trust]}>
        {info.label}
      </Badge>
    </Explained>
  );
}

export function SensitivityBadge({ sensitivity }: { sensitivity: Sensitivity }) {
  const info = SENSITIVITY_LABELS[sensitivity] ?? { label: sensitivity, description: "" };
  return (
    <Explained tip={info.description}>
      <Badge
        variant={sensitivity === "restricted" ? "destructive" : "outline"}
        className={cn(
          sensitivity === "confidential" && "border-amber-500/50 text-amber-700 dark:text-amber-400",
          sensitivity === "restricted" && "font-semibold",
        )}
      >
        {sensitivity === "restricted" ? <Lock data-icon="inline-start" aria-hidden="true" /> : null}
        {info.label}
      </Badge>
    </Explained>
  );
}

const STATUS_ICON: Record<DocumentStatus, { icon: LucideIcon; className: string }> = {
  pending: { icon: CircleDashed, className: "text-muted-foreground" },
  processing: { icon: LoaderCircle, className: "animate-spin text-sky-600 dark:text-sky-400" },
  ready: { icon: CircleCheck, className: "text-emerald-600 dark:text-emerald-400" },
  failed: { icon: CircleX, className: "text-destructive" },
};

export function DocumentStatusLabel({ status }: { status: DocumentStatus }) {
  const { icon: Icon, className } = STATUS_ICON[status] ?? STATUS_ICON.pending;
  const info = STATUS_LABELS[status] ?? { label: status, description: "" };
  return (
    <span className="inline-flex items-center gap-1.5 text-sm" title={info.description}>
      <Icon className={cn("size-4", className)} aria-hidden="true" />
      {info.label}
    </span>
  );
}

type FileKind = "json" | "html" | "sheet" | "slides" | "text" | "other";

export function fileKind(mime: string): FileKind {
  if (mime === "application/json") return "json";
  if (mime === "text/html") return "html";
  if (mime === "text/csv" || mime.includes("spreadsheet")) return "sheet";
  if (mime.includes("presentation")) return "slides";
  if (mime.startsWith("text/") || mime === "application/pdf" || mime.includes("wordprocessing")) return "text";
  return "other";
}

const FILE_ICONS: Record<FileKind, LucideIcon> = {
  json: FileBraces,
  html: FileCode,
  sheet: FileSpreadsheet,
  slides: Presentation,
  text: FileText,
  other: File,
};

export function FileTypeIcon({ mime, className }: { mime: string; className?: string }) {
  const props = { className: cn("size-4 text-muted-foreground", className), "aria-hidden": true as const };
  return (
    <span title={fileTypeLabel(mime)} className="inline-flex">
      {createElement(FILE_ICONS[fileKind(mime)], props)}
      <span className="sr-only">{fileTypeLabel(mime)}</span>
    </span>
  );
}
