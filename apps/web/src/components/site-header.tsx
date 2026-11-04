"use client";

import { usePathname } from "next/navigation";
import { Suspense } from "react";

import { ThemeToggle } from "@/components/theme-toggle";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { UserMenu } from "@/components/user-menu";
import { activeNavItem, siteConfig } from "@/config/site";

function SectionTitle() {
  const section = activeNavItem(usePathname());
  return <h1 className="truncate text-sm font-medium">{section?.title ?? siteConfig.name}</h1>;
}

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b bg-background/80 px-4 backdrop-blur supports-backdrop-filter:bg-background/60">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mx-1 data-[orientation=vertical]:h-4" />
      {/* usePathname is runtime data on dynamic routes; the prerendered shell shows the product name. */}
      <Suspense fallback={<h1 className="truncate text-sm font-medium">{siteConfig.name}</h1>}>
        <SectionTitle />
      </Suspense>

      <div className="ml-auto flex items-center gap-2">
        <Badge variant="outline" className="uppercase tracking-wide">
          {siteConfig.stage}
        </Badge>
        <ThemeToggle />
        <UserMenu />
      </div>
    </header>
  );
}
