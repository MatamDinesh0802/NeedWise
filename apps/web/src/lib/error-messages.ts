/**
 * Turns API errors into messages people can act on. Framework-free and unit-tested.
 * The API sends problem+json with a machine-readable `code` (see lib/api.ts).
 */

import { ApiError } from "@/lib/api";

export interface ErrorMessage {
  title: string;
  description: string;
  code: string | null;
}

const MESSAGES: Record<string, Omit<ErrorMessage, "code">> = {
  // 409 Conflict
  email_taken: {
    title: "Email already in use",
    description: "Someone in this organization already has this email address.",
  },
  slug_taken: {
    title: "Slug already in use",
    description: "Another item already uses this slug or key. Choose a different one.",
  },
  last_owner: {
    title: "The organization needs an owner",
    description: "This is the last active owner. Make someone else an owner first.",
  },
  group_cycle: {
    title: "Groups can't contain each other",
    description: "Adding this group would create a loop, because it already contains this group (directly or through other groups).",
  },
  // 403 Forbidden
  owner_required: {
    title: "Only an owner can do this",
    description: "Granting, removing, disabling or enabling the owner role requires an owner.",
  },
  self_change: {
    title: "You can't change your own account here",
    description: "Ask another administrator to change your role or status.",
  },
  forbidden: {
    title: "You don't have access",
    description: "Your organization role doesn't allow this action.",
  },
  csrf_origin: {
    title: "Request blocked",
    description: "The request didn't come from this site. Reload the page and try again.",
  },
  csrf_header: {
    title: "Request blocked",
    description: "The request was missing a security header. Reload the page and try again.",
  },
  // 422 business rules
  department_required: {
    title: "Choose a department",
    description: "A department collection belongs to one department; pick it before saving.",
  },
  viewers_required: {
    title: "Choose who can view",
    description: "A restricted collection needs at least one person or group who can view it.",
  },
  unknown_collection: {
    title: "Unknown collection",
    description: "One of the chosen collections no longer exists. Refresh the page and choose again.",
  },
  no_version: {
    title: "Nothing to re-index",
    description: "This document has no stored version yet. Upload the file again.",
  },
  // Other
  unauthenticated: {
    title: "You're signed out",
    description: "Your session ended. Sign in again to continue.",
  },
  not_found: {
    title: "Not found",
    description: "It may have been deleted. Refresh the page to see the latest data.",
  },
  network_error: {
    title: "Can't reach the server",
    description: "Check your connection or try again in a moment.",
  },
};

export interface FieldError {
  field: string;
  message: string;
}

const PATTERN_HINTS: Record<string, string> = {
  email: "Enter a valid email address.",
  slug: "Use only lowercase letters, numbers and single hyphens.",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Field-level errors from a 422 `validation_error` (FastAPI's error list). */
export function fieldErrors(error: unknown): FieldError[] {
  if (!(error instanceof ApiError) || error.code !== "validation_error") return [];
  const list = error.problem?.errors;
  if (!Array.isArray(list)) return [];
  return list.filter(isRecord).map((item) => {
    const loc = Array.isArray(item.loc) ? item.loc : [];
    const path = loc.filter((part) => part !== "body" && part !== "query" && part !== "path");
    const field = path.length > 0 ? String(path[path.length - 1]) : "request";
    const message =
      item.type === "string_pattern_mismatch" && PATTERN_HINTS[field]
        ? PATTERN_HINTS[field]
        : typeof item.msg === "string"
          ? item.msg
          : "Invalid value.";
    return { field, message };
  });
}

/** Message for any error thrown by the API wrapper (or anything else). */
export function errorMessage(error: unknown): ErrorMessage {
  if (error instanceof ApiError) {
    const code = error.code;
    if (code && Object.hasOwn(MESSAGES, code)) return { ...MESSAGES[code], code };
    if (code === "validation_error") {
      const fields = fieldErrors(error);
      return {
        title: "Check the highlighted fields",
        description:
          fields.length > 0
            ? fields.map((f) => `${f.field}: ${f.message}`).join(" ")
            : (error.detail ?? "Some values are not valid."),
        code,
      };
    }
    return {
      title: error.title || "Something went wrong",
      description: error.detail ?? (error.status >= 500 ? "The server had a problem. Try again." : "Please try again."),
      code,
    };
  }
  return {
    title: "Something went wrong",
    description: error instanceof Error && error.message ? error.message : "Please try again.",
    code: null,
  };
}
