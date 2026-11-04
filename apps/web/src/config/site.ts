import {
  Activity,
  Bot,
  CalendarClock,
  ChartColumnBig,
  LayoutDashboard,
  LibraryBig,
  MessageSquareText,
  Settings,
  ShieldCheck,
  Workflow,
  type LucideIcon,
} from "lucide-react";

import { ADMIN_VIEW_ROLES, type OrgRole } from "@/lib/session";

/**
 * Single source of truth for product identity and navigation.
 * The product name may change — nothing else in the app should hardcode it.
 */
export const siteConfig = {
  name: "WorkMind AI",
  shortName: "WorkMind",
  tagline: "Permission-aware AI workspace for your organization",
  shortTagline: "Enterprise AI workspace",
  description:
    "Open-source, self-hostable AI workspace: cited answers from company knowledge, specialized agents, and human-approved workflows.",
  stage: "pre-alpha",
} as const;

export type NavGroup = "workspace" | "administration" | "account";

export interface NavItem {
  title: string;
  href: `/${string}`;
  icon: LucideIcon;
  /** One line describing what this section will do. */
  description: string;
  /** Roadmap phase in which the section becomes functional (see docs/ROADMAP.md). */
  phase: number;
  group: NavGroup;
  /**
   * If set, only these org roles see the item in the navigation.
   * Cosmetic only: hiding a link is not access control; the API enforces permissions.
   */
  visibleTo?: readonly OrgRole[];
}

export const navItems: readonly NavItem[] = [
  {
    title: "Overview",
    href: "/",
    icon: LayoutDashboard,
    description: "Your recent activity, tasks, commitments and workspace usage at a glance.",
    phase: 4,
    group: "workspace",
  },
  {
    title: "Assistant",
    href: "/assistant",
    icon: MessageSquareText,
    description: "Ask questions and get cited answers from sources you are allowed to see.",
    phase: 4,
    group: "workspace",
  },
  {
    title: "Knowledge Hub",
    href: "/knowledge",
    icon: LibraryBig,
    description: "Collections, documents and connected sources, with live ingestion status.",
    phase: 2,
    group: "workspace",
  },
  {
    title: "AI Agents",
    href: "/agents",
    icon: Bot,
    description: "Department agents, their tools and knowledge scopes, and a test console.",
    phase: 5,
    group: "workspace",
  },
  {
    title: "Workflows",
    href: "/workflows",
    icon: Workflow,
    description: "Tasks, scheduled automations and the approvals inbox for agent actions.",
    phase: 8,
    group: "workspace",
  },
  {
    title: "Meetings",
    href: "/meetings",
    icon: CalendarClock,
    description: "Summaries, decisions and action items from transcripts and recordings.",
    phase: 9,
    group: "workspace",
  },
  {
    title: "Insights",
    href: "/insights",
    icon: ChartColumnBig,
    description: "Ask analytical questions about company data and get charts with verifiable SQL.",
    phase: 10,
    group: "workspace",
  },
  {
    title: "Analytics",
    href: "/analytics",
    icon: Activity,
    description: "Usage, cost, agent performance and retrieval quality across the workspace.",
    phase: 11,
    group: "workspace",
  },
  {
    title: "Admin",
    href: "/admin",
    icon: ShieldCheck,
    description: "Organization, users, departments, groups, connectors, policies and audit log.",
    phase: 1,
    group: "administration",
    visibleTo: ADMIN_VIEW_ROLES,
  },
  {
    title: "Settings",
    href: "/settings",
    icon: Settings,
    description: "Your profile, personal access tokens and assistant memory.",
    phase: 1,
    group: "account",
  },
];

export function navItemsByGroup(group: NavGroup): NavItem[] {
  return navItems.filter((item) => item.group === group);
}

/** Whether a user with `role` (undefined while loading or signed out) sees `item`. */
export function isNavItemVisible(item: NavItem, role: OrgRole | null | undefined): boolean {
  return item.visibleTo === undefined || (role != null && item.visibleTo.includes(role));
}

export function visibleNavItems(group: NavGroup, role: OrgRole | null | undefined): NavItem[] {
  return navItemsByGroup(group).filter((item) => isNavItemVisible(item, role));
}

export function findNavItem(href: string): NavItem | undefined {
  return navItems.find((item) => item.href === href);
}

/** Nav item whose section contains `pathname` (longest matching prefix wins). */
export function activeNavItem(pathname: string): NavItem | undefined {
  return [...navItems]
    .filter((item) =>
      item.href === "/"
        ? pathname === "/"
        : pathname === item.href || pathname.startsWith(`${item.href}/`),
    )
    .sort((a, b) => b.href.length - a.href.length)[0];
}
