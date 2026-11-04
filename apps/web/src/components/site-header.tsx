"use client";

import { LogIn } from "lucide-react";
import { usePathname } from "next/navigation";

import { ThemeToggle } from "@/components/theme-toggle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { activeNavItem, siteConfig } from "@/config/site";

export function SiteHeader() {
  const pathname = usePathname();
  const section = activeNavItem(pathname);

  return (
    <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b bg-background/80 px-4 backdrop-blur supports-backdrop-filter:bg-background/60">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mx-1 data-[orientation=vertical]:h-4" />
      <h1 className="truncate text-sm font-medium">{section?.title ?? siteConfig.name}</h1>

      <div className="ml-auto flex items-center gap-2">
        <Badge variant="outline" className="uppercase tracking-wide">
          {siteConfig.stage}
        </Badge>
        <ThemeToggle />
        <Button
          size="sm"
          disabled
          title="Sign-in arrives in Phase 1"
          aria-label="Sign in (available in Phase 1)"
        >
          <LogIn data-icon="inline-start" />
          Sign in
        </Button>
      </div>
    </header>
  );
}
