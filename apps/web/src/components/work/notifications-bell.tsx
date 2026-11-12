"use client";

import { Bell, CircleAlert, CircleCheck, Info, ListTodo, ShieldQuestion, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { useSession } from "@/hooks/use-session";
import { useStore } from "@/hooks/use-store";
import { formatDateTime, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Notification } from "@/lib/work/api";
import {
  badgeLabel,
  bellLabel,
  getNotificationsStore,
  notificationHref,
  notificationTone,
  unreadCount,
  type NotificationTone,
} from "@/lib/work/notifications";

const TONES: Record<NotificationTone, { icon: LucideIcon; className: string }> = {
  approval: { icon: ShieldQuestion, className: "text-amber-600 dark:text-amber-400" },
  done: { icon: CircleCheck, className: "text-emerald-600 dark:text-emerald-400" },
  problem: { icon: CircleAlert, className: "text-destructive" },
  task: { icon: ListTodo, className: "text-sky-600 dark:text-sky-400" },
  info: { icon: Info, className: "text-muted-foreground" },
};

/** The header bell. Only for signed-in people; one shared poller (every 30 s, and on focus). */
export function NotificationsBell() {
  const { state } = useSession();
  if (state.kind !== "signed_in") return null;
  return <BellPopover />;
}

function BellPopover() {
  const store = getNotificationsStore();
  const { status, items } = useStore(store);
  const [open, setOpen] = useState(false);
  const unread = unreadCount(items);
  const badge = badgeLabel(unread);

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) void store.refresh();
      }}
    >
      <PopoverTrigger
        render={
          <Button variant="ghost" size="icon" className="relative" aria-label={bellLabel(unread)} title={bellLabel(unread)}>
            <Bell />
            {badge ? (
              <span
                aria-hidden="true"
                className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] leading-none font-semibold text-white tabular-nums"
              >
                {badge}
              </span>
            ) : null}
          </Button>
        }
      />
      <PopoverContent align="end" className="w-[min(24rem,calc(100vw-2rem))] gap-0 p-0">
        <div className="flex items-center justify-between gap-2 border-b px-3 py-2">
          <p className="text-sm font-semibold">Notifications</p>
          <Button variant="ghost" size="xs" disabled={unread === 0} onClick={() => void store.markRead()}>
            Mark all as read
          </Button>
        </div>
        {status === "loading" || status === "idle" ? (
          <div className="flex flex-col gap-2 p-3" aria-busy="true">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : status === "error" && items.length === 0 ? (
          <p className="p-4 text-sm text-muted-foreground">Couldn&apos;t load notifications. They&apos;ll retry in a moment.</p>
        ) : items.length === 0 ? (
          <p className="p-6 text-center text-sm text-muted-foreground">You&apos;re all caught up.</p>
        ) : (
          <ul className="max-h-[60vh] overflow-y-auto py-1">
            {items.map((n) => (
              <li key={n.id}>
                <NotificationItem
                  notification={n}
                  onOpen={() => {
                    if (n.read_at === null) void store.markRead([n.id]);
                    setOpen(false);
                  }}
                />
              </li>
            ))}
          </ul>
        )}
        <div className="border-t px-3 py-2">
          <Link href="/approvals" onClick={() => setOpen(false)} className={cn(buttonVariants({ variant: "ghost", size: "xs" }), "w-full")}>
            Open the approvals inbox
          </Link>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function NotificationItem({ notification: n, onOpen }: { notification: Notification; onOpen: () => void }) {
  const tone = TONES[notificationTone(n.kind)];
  const Icon = tone.icon;
  const unread = n.read_at === null;
  const body = (
    <>
      <Icon className={cn("mt-0.5 size-4 shrink-0", tone.className)} aria-hidden="true" />
      <span className="min-w-0 flex-1">
        <span className={cn("block text-sm break-words", unread ? "font-medium" : "text-muted-foreground")}>{n.title}</span>
        {n.body ? <span className="line-clamp-2 block text-xs text-muted-foreground">{n.body}</span> : null}
        <span className="block text-[11px] text-muted-foreground" title={formatDateTime(n.created_at)}>
          {formatRelative(n.created_at)}
        </span>
      </span>
      {unread ? <span className="mt-1.5 size-2 shrink-0 rounded-full bg-sky-500" aria-label="unread" /> : null}
    </>
  );
  const className = cn("flex w-full items-start gap-2.5 px-3 py-2 text-left hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none", unread && "bg-sky-500/5");
  const href = notificationHref(n.link);
  return href ? (
    <Link href={href} className={className} onClick={onOpen}>
      {body}
    </Link>
  ) : (
    <button type="button" className={className} onClick={onOpen}>
      {body}
    </button>
  );
}
