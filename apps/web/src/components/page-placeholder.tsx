import type { Metadata } from "next";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { findNavItem, siteConfig, type NavItem } from "@/config/site";

function requireNavItem(href: NavItem["href"]): NavItem {
  const item = findNavItem(href);
  if (!item) throw new Error(`No nav item configured for "${href}" in src/config/site.ts`);
  return item;
}

/** Page metadata derived from the nav config, so titles never drift from the sidebar. */
export function sectionMetadata(href: NavItem["href"]): Metadata {
  const item = requireNavItem(href);
  // The root layout's title template doesn't apply to "/" (same segment), so spell it out.
  const title = href === "/" ? { absolute: `${item.title} · ${siteConfig.name}` } : item.title;
  return { title, description: item.description };
}

/**
 * Placeholder for sections that are not built yet.
 * Title, description and roadmap phase come from src/config/site.ts.
 */
export function PagePlaceholder({ href, children }: { href: NavItem["href"]; children?: ReactNode }) {
  const item = requireNavItem(href);
  const Icon = item.icon;

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6">
      <section className="flex flex-col gap-4 rounded-xl border border-dashed bg-muted/30 p-6 sm:flex-row sm:items-start">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-lg border bg-background">
          <Icon className="size-5 text-muted-foreground" aria-hidden="true" />
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold tracking-tight">{item.title}</h2>
            <Badge variant="secondary">Arrives in Phase {item.phase}</Badge>
          </div>
          <p className="text-sm text-muted-foreground">{item.description}</p>
        </div>
      </section>
      {children}
    </div>
  );
}
