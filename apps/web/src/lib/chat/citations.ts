/**
 * Citation markers in answers: "[S1]" and "[S1, S3]". Only plain text is touched;
 * markers inside inline code or code blocks stay as written (see remarkCitations).
 */

const MARKERS = String.raw`\[(S\d+(?:\s*,\s*S\d+)*)\]`;
/** Non-global: safe to share. */
export const HAS_CITATION = new RegExp(MARKERS);

export type TextSegment = { type: "text"; value: string } | { type: "cite"; markers: string[] };

export function splitCitations(text: string): TextSegment[] {
  const segments: TextSegment[] = [];
  let last = 0;
  for (const match of text.matchAll(new RegExp(MARKERS, "g"))) {
    const start = match.index ?? 0;
    if (start > last) segments.push({ type: "text", value: text.slice(last, start) });
    const markers = match[1].split(",").map((m) => m.trim());
    segments.push({ type: "cite", markers: [...new Set(markers)] });
    last = start + match[0].length;
  }
  if (last < text.length) segments.push({ type: "text", value: text.slice(last) });
  return segments;
}

/** Every marker the answer cites, in first-appearance order. */
export function citedMarkers(text: string): string[] {
  const seen: string[] = [];
  for (const segment of splitCitations(text)) {
    if (segment.type === "cite") for (const m of segment.markers) if (!seen.includes(m)) seen.push(m);
  }
  return seen;
}

/** "S12" → 12, for ordering sources. */
export function markerSortKey(marker: string): number {
  const n = Number(marker.replace(/^S/, ""));
  return Number.isFinite(n) ? n : Number.MAX_SAFE_INTEGER;
}

// --- remark plugin --------------------------------------------------------------------------

interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
  data?: { hName?: string; hProperties?: Record<string, unknown>; hChildren?: unknown[] };
}

/** Node types whose text must never become chips. */
const SKIP = new Set(["code", "inlineCode", "html", "link", "linkReference", "definition"]);

function transform(node: MdNode): void {
  if (!node.children) return;
  const next: MdNode[] = [];
  for (const child of node.children) {
    if (child.type === "text" && typeof child.value === "string" && HAS_CITATION.test(child.value)) {
      for (const segment of splitCitations(child.value)) {
        if (segment.type === "text") next.push({ type: "text", value: segment.value });
        else
          next.push({
            type: "citation",
            children: [],
            data: { hName: "cite", hProperties: { dataMarkers: segment.markers.join(" ") }, hChildren: [] },
          });
      }
    } else {
      if (!SKIP.has(child.type)) transform(child);
      next.push(child);
    }
  }
  node.children = next;
}

/** remark plugin: turns markers in text nodes into <cite data-markers="S1 S3"> elements. */
export function remarkCitations() {
  return (tree: MdNode) => {
    transform(tree);
  };
}
