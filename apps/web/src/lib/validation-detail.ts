/**
 * Some API errors (422 invalid_input, invalid_workflow, invalid_config) carry Pydantic's
 * ValidationError text in `detail`, cut to a few hundred characters:
 *
 *     2 validation errors for SendEmail
 *     to.0
 *       String should match pattern '^[^@\s]+@…' [type=string_pattern_mismatch, input_value='bob', …]
 *         For further information visit https://errors.pydantic.dev/2.11/v/string_pattern_mismatch
 *     subject
 *       String should have at least 1 character [type=string_too_short, …]
 *
 * This turns it into field errors people can read. Anything else becomes one general error.
 */

import { ApiError } from "@/lib/api";

export interface DetailError {
  /** Dotted location ("steps.1.tool", "to.0"), or "" when it applies to the whole value. */
  path: string;
  /** The first path segment ("to", "steps"): which form field to highlight. */
  field: string;
  message: string;
}

const HEADER = /^\d+ validation errors? for \S+/;
const INFO = /^For further information/;
const PATTERN_MISMATCH = /^String should match pattern/;

function clean(message: string): string {
  const withoutMeta = message.replace(/\s*\[type=.*$/, "").trim(); // also when the detail was cut off
  // "Value error, step send: give either …" reads better without the prefix.
  const plain = withoutMeta.replace(/^(Value|Assertion) error,\s*/, "");
  if (PATTERN_MISMATCH.test(plain)) return "Not in the expected format.";
  return plain.charAt(0).toUpperCase() + plain.slice(1);
}

export function parseValidationDetail(detail: string | null | undefined): DetailError[] {
  const text = (detail ?? "").trim();
  if (!text) return [];
  const lines = text.split("\n");
  if (!HEADER.test(lines[0].trim())) {
    return [{ path: "", field: "", message: clean(text) }];
  }
  const errors: DetailError[] = [];
  let path: string | null = null;
  for (const raw of lines.slice(1)) {
    if (!raw.trim()) continue;
    const indent = raw.length - raw.trimStart().length;
    const line = raw.trim();
    if (INFO.test(line)) continue;
    if (indent === 0) {
      path = line;
      continue;
    }
    if (path === null) continue;
    const loc = path === "__root__" ? "" : path;
    errors.push({ path: loc, field: loc.split(".")[0] ?? "", message: clean(line) });
    path = null; // one message per location; continuation lines (cut-off text) are dropped
  }
  return errors.length > 0 ? errors : [{ path: "", field: "", message: clean(lines.slice(1).join(" ") || lines[0]) }];
}

/** "steps.1.tool" → "Step 2 › tool", "to.0" → "To (1st)". Readable locations for a list. */
export function describePath(path: string): string {
  if (!path) return "";
  const parts = path.split(".");
  const out: string[] = [];
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    const next = parts[i + 1];
    if (next !== undefined && /^\d+$/.test(next)) {
      const label = part === "steps" ? "Step" : part.charAt(0).toUpperCase() + part.slice(1);
      out.push(`${label} ${Number(next) + 1}`);
      i++;
    } else if (/^\d+$/.test(part)) {
      out.push(`item ${Number(part) + 1}`);
    } else {
      out.push(part);
    }
  }
  return out.join(" › ");
}

/** Field errors from an ApiError whose `detail` is Pydantic text (for the given codes). */
export function detailErrors(error: unknown, codes: readonly string[]): DetailError[] {
  if (!(error instanceof ApiError) || !error.code || !codes.includes(error.code)) return [];
  return parseValidationDetail(error.detail);
}
