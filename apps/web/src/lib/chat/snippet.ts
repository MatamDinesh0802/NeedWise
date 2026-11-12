/**
 * A source passage as the Sources panel shows it. Documents' tables are indexed as markdown
 * tables (parsing.py writes "| a | b |" rows under a "|---|" line), which read as a wall of pipes;
 * those are parsed into rows to draw as a table. Everything else stays text. Unit-tested.
 */

export type SnippetPart = { kind: "text"; text: string } | { kind: "table"; header: string[]; rows: string[][] };

const ROW = /^\s*\|.*\|\s*$/;
const RULE = /^\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*$/;

function cells(line: string): string[] {
  return line
    .trim()
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((cell) => cell.trim());
}

/** Text and markdown tables, in order. A table is a header row, a |---| rule and its rows. */
export function snippetParts(text: string): SnippetPart[] {
  const lines = text.split(/\r?\n/);
  const parts: SnippetPart[] = [];
  let buffer: string[] = [];
  const flush = () => {
    const joined = buffer.join("\n").trim();
    if (joined) parts.push({ kind: "text", text: joined });
    buffer = [];
  };
  for (let i = 0; i < lines.length; i++) {
    if (ROW.test(lines[i]) && i + 1 < lines.length && RULE.test(lines[i + 1])) {
      flush();
      const header = cells(lines[i]);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && ROW.test(lines[i])) {
        rows.push(cells(lines[i]));
        i++;
      }
      i--;
      parts.push({ kind: "table", header, rows });
      continue;
    }
    buffer.push(lines[i]);
  }
  flush();
  return parts;
}
