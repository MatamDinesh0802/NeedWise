/**
 * Markdown images are never loaded. An answer's text is written by a model that has read data
 * (query results, document excerpts), and that data can talk it into writing
 * `![x](https://host/?d=<what it read>)`: the browser would send it to that host as soon as the
 * answer is shown. Only the alt text is rendered, in brackets.
 */
export function MarkdownImage({ alt }: { alt?: string; node?: unknown }) {
  const text = alt?.trim();
  return text ? <span className="text-muted-foreground">[{text}]</span> : null;
}
