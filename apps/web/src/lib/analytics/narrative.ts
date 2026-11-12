/**
 * The answer's narrative: [Q1] / [Q1, Q2] markers become links to those queries, and every number
 * the API checked (verification.numbers) is marked verified or unverified where it appears.
 * Only plain text is touched; code spans and code blocks stay as written. Unit-tested.
 */

import type { VerifiedNumber } from "@/lib/analytics/api";

export type NarrativeSegment =
  | { type: "text"; value: string }
  | { type: "queries"; ids: string[] }
  /** `index` points into verification.numbers. */
  | { type: "number"; text: string; index: number };

const QUERY_MARKERS = String.raw`\[(Q\d+(?:\s*,\s*Q\d+)*)\]`;
const HAS_QUERY = new RegExp(QUERY_MARKERS);

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const WORD = /[\wÀ-ɏ]/;
const DIGIT = /\d/;

/** A number match must not be part of a longer number or word ("12%" inside "112%", "5" in "Q5"). */
function standsAlone(text: string, start: number, end: number): boolean {
  const before = text[start - 1];
  const after = text[end];
  if (before !== undefined) {
    if (WORD.test(before)) return false;
    if ((before === "." || before === ",") && DIGIT.test(text[start - 2] ?? "")) return false;
  }
  if (after !== undefined) {
    if (WORD.test(after)) return false;
    if ((after === "." || after === ",") && DIGIT.test(text[end + 1] ?? "")) return false;
  }
  return true;
}

/**
 * Hands out verification entries in narrative order: the n-th "€5.02M" in the text is the n-th
 * entry with that text (the API lists numbers as they appear). A text that occurs more often than
 * it was listed reuses its last entry. Create one per rendered document.
 */
export function numberMatcher(numbers: readonly VerifiedNumber[]) {
  const queues = new Map<string, number[]>();
  numbers.forEach((n, index) => {
    const text = n.text.trim();
    if (!text) return;
    const queue = queues.get(text) ?? [];
    queue.push(index);
    queues.set(text, queue);
  });
  const used = new Map<string, number>();
  const texts = [...queues.keys()].sort((a, b) => b.length - a.length); // longest first: "€5.02M" before "5.02"
  const pattern = texts.length > 0 ? new RegExp(`${QUERY_MARKERS}|(${texts.map(escapeRegExp).join("|")})`, "g") : new RegExp(QUERY_MARKERS, "g");

  function next(text: string): number {
    const queue = queues.get(text)!;
    const n = used.get(text) ?? 0;
    used.set(text, n + 1);
    return queue[Math.min(n, queue.length - 1)];
  }

  /** Splits one run of plain text. */
  function split(text: string): NarrativeSegment[] {
    const segments: NarrativeSegment[] = [];
    let last = 0;
    pattern.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(text)) !== null) {
      const start = match.index;
      const end = start + match[0].length;
      if (match[1] === undefined && !standsAlone(text, start, end)) {
        pattern.lastIndex = start + 1;
        continue;
      }
      if (start > last) segments.push({ type: "text", value: text.slice(last, start) });
      if (match[1] !== undefined) {
        segments.push({ type: "queries", ids: [...new Set(match[1].split(",").map((id) => id.trim()))] });
      } else {
        segments.push({ type: "number", text: match[0], index: next(match[0]) });
      }
      last = end;
    }
    if (last < text.length) segments.push({ type: "text", value: text.slice(last) });
    return segments;
  }

  return { split, hasNumbers: texts.length > 0 };
}

/** Splits plain text once (no markdown). Handy for tests and plain-text views. */
export function splitNarrative(text: string, numbers: readonly VerifiedNumber[]): NarrativeSegment[] {
  return numberMatcher(numbers).split(text);
}

/** Every query id the narrative refers to, in first-appearance order. */
export function referencedQueries(text: string): string[] {
  const ids: string[] = [];
  for (const match of text.matchAll(new RegExp(QUERY_MARKERS, "g"))) {
    for (const id of match[1].split(",").map((s) => s.trim())) if (!ids.includes(id)) ids.push(id);
  }
  return ids;
}

export function hasQueryMarker(text: string): boolean {
  return HAS_QUERY.test(text);
}

// --- remark plugin --------------------------------------------------------------------------

interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
  data?: { hName?: string; hProperties?: Record<string, unknown>; hChildren?: unknown[] };
}

/** Node types whose text is never marked. */
const SKIP = new Set(["code", "inlineCode", "html", "link", "linkReference", "definition"]);

/**
 * remark plugin factory: [Qn] markers become <cite data-queries="Q1 Q2"> and checked numbers
 * become <data value="…" data-index="…">€5.02M</data>; the renderer styles both.
 */
export function remarkNarrative(numbers: readonly VerifiedNumber[]) {
  return () => (tree: MdNode) => {
    const matcher = numberMatcher(numbers);
    const visit = (node: MdNode) => {
      if (!node.children) return;
      const next: MdNode[] = [];
      for (const child of node.children) {
        if (child.type === "text" && typeof child.value === "string") {
          for (const segment of matcher.split(child.value)) {
            if (segment.type === "text") next.push({ type: "text", value: segment.value });
            else if (segment.type === "queries")
              next.push({
                type: "queryRef",
                children: [],
                data: { hName: "cite", hProperties: { dataQueries: segment.ids.join(" ") }, hChildren: [] },
              });
            else
              next.push({
                type: "checkedNumber",
                children: [{ type: "text", value: segment.text }],
                data: {
                  hName: "data",
                  hProperties: { value: String(numbers[segment.index]?.value ?? ""), dataIndex: String(segment.index) },
                },
              });
          }
        } else {
          if (!SKIP.has(child.type)) visit(child);
          next.push(child);
        }
      }
      node.children = next;
    };
    visit(tree);
  };
}

/** Numbers that never appear in the narrative text (so they can't be marked in place). */
export function unplacedNumbers(text: string, numbers: readonly VerifiedNumber[]): number[] {
  const placed = new Set(
    numberMatcher(numbers)
      .split(text)
      .filter((s): s is Extract<NarrativeSegment, { type: "number" }> => s.type === "number")
      .map((s) => s.index),
  );
  return numbers.map((_, i) => i).filter((i) => !placed.has(i));
}

/** Plain words for the verification summary. */
export function verificationSummary(verified: number, total: number): string {
  if (total === 0) return "The text states no figures to check.";
  if (verified === total) return total === 1 ? "The one figure in the text matches the query results." : `All ${total} figures in the text match the query results.`;
  if (verified === 0) return total === 1 ? "The figure in the text could not be matched to the query results." : `None of the ${total} figures in the text could be matched to the query results.`;
  return `${verified} of ${total} figures in the text match the query results; ${total - verified} could not be matched.`;
}

export const UNVERIFIED_EXPLANATION =
  "Not found in any query result. It may be the model's own arithmetic (a sum, a share, a growth rate) or a mistake: check it against the tables below before you rely on it.";
