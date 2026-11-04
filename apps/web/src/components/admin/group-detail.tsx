"use client";

import { ArrowLeft, FolderTree, SearchX, UserMinus, Users } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { PersonPicker } from "@/components/admin/person-picker";
import { SimpleSelect } from "@/components/data/form";
import { EmptyState, ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { adminApi, adminKeys, type Group, type GroupMember } from "@/lib/admin/api";

function refreshAfterMembershipChange(groupId: string) {
  return Promise.all([
    invalidateQueries(adminKeys.groupMembers(groupId)),
    invalidateQueries(adminKeys.groups),
    invalidateQueries(adminKeys.users),
  ]);
}

export function GroupDetail() {
  const { id } = useParams<{ id: string }>();
  const groups = useApiQuery(adminKeys.groups, adminApi.listGroups);

  const back = (
    <Link href="/admin/groups" className={buttonVariants({ variant: "ghost", size: "sm", className: "w-fit -ml-2" })}>
      <ArrowLeft data-icon="inline-start" />
      Groups
    </Link>
  );

  if (groups.state.status === "loading") {
    return (
      <div className="flex flex-col gap-4">
        {back}
        <Skeleton className="h-8 w-64" />
        <TableSkeleton />
      </div>
    );
  }
  if (groups.state.status === "error") {
    return (
      <div className="flex flex-col gap-4">
        {back}
        <ErrorAlert error={groups.state.error} onRetry={() => void groups.refetch()} />
      </div>
    );
  }
  const all = groups.state.data.items;
  const group = all.find((g) => g.id === id);
  if (!group) {
    return (
      <div className="flex flex-col gap-4">
        {back}
        <EmptyState icon={SearchX} title="Group not found" description="It may have been deleted." />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {back}
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-lg font-semibold">{group.name}</h3>
        <Badge variant="outline">{group.source}</Badge>
      </div>
      <p className="-mt-3 font-mono text-xs text-muted-foreground">{group.slug}</p>
      {group.description ? <p className="text-sm text-muted-foreground">{group.description}</p> : null}
      <Members group={group} allGroups={all} />
    </div>
  );
}

function MemberList({
  title,
  icon,
  items,
  emptyText,
  busy,
  onRemove,
}: {
  title: string;
  icon: typeof Users;
  items: GroupMember[];
  emptyText: string;
  busy: boolean;
  onRemove: (member: GroupMember) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <h4 className="text-sm font-medium">
        {title} <span className="text-muted-foreground">({items.length})</span>
      </h4>
      {items.length === 0 ? (
        <EmptyState icon={icon} title={emptyText} />
      ) : (
        <ul className="flex flex-col rounded-lg border">
          {items.map((member) => (
            <li key={`${member.kind}:${member.id}`} className="flex items-center justify-between gap-3 border-b px-3 py-2 last:border-0">
              <span className="flex min-w-0 flex-col">
                {member.kind === "group" ? (
                  <Link href={`/admin/groups/${member.id}`} className="truncate font-medium hover:underline">
                    {member.name}
                  </Link>
                ) : (
                  <span className="truncate font-medium">{member.name}</span>
                )}
                {member.email ? <span className="truncate text-xs text-muted-foreground">{member.email}</span> : null}
              </span>
              <Button
                variant="ghost"
                size="icon-sm"
                disabled={busy}
                aria-label={`Remove ${member.name}`}
                title="Remove from group"
                onClick={() => onRemove(member)}
              >
                <UserMinus />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Members({ group, allGroups }: { group: Group; allGroups: Group[] }) {
  const { state, refetch } = useApiQuery(adminKeys.groupMembers(group.id), () => adminApi.listGroupMembers(group.id));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [childId, setChildId] = useState("");

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await refreshAfterMembershipChange(group.id);
      return true;
    } catch (err) {
      setError(err);
      return false;
    } finally {
      setBusy(false);
    }
  }

  const members = state.status === "success" ? state.data.items : [];
  const users = members.filter((m) => m.kind === "user");
  const nested = members.filter((m) => m.kind === "group");
  const userIds = new Set(users.map((u) => u.id));
  const nestedIds = new Set(nested.map((g) => g.id));
  const candidates = allGroups.filter((g) => g.id !== group.id && !nestedIds.has(g.id));
  const remove = (member: GroupMember) =>
    void run(() =>
      member.kind === "user" ? adminApi.removeGroupUser(group.id, member.id) : adminApi.removeGroupChild(group.id, member.id),
    );

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-2">
          <h4 className="font-medium">Members</h4>
          <RefreshingHint active={state.status !== "loading" && state.refreshing} />
        </div>
        {error ? <ErrorAlert error={error} /> : null}
        {state.status === "loading" ? (
          <TableSkeleton rows={3} />
        ) : state.status === "error" ? (
          <ErrorAlert error={state.error} onRetry={() => void refetch()} />
        ) : (
          <>
            <MemberList title="People" icon={Users} items={users} emptyText="No people in this group" busy={busy} onRemove={remove} />
            <MemberList
              title="Nested groups"
              icon={FolderTree}
              items={nested}
              emptyText="No nested groups"
              busy={busy}
              onRemove={remove}
            />
          </>
        )}
      </div>

      <div className="flex flex-col gap-4">
        <Card size="sm">
          <CardHeader>
            <CardTitle>Add person</CardTitle>
          </CardHeader>
          <CardContent>
            <PersonPicker exclude={userIds} disabled={busy} onPick={(user) => void run(() => adminApi.addGroupUser(group.id, user.id))} />
          </CardContent>
        </Card>
        <Card size="sm">
          <CardHeader>
            <CardTitle>Add nested group</CardTitle>
            <CardDescription>Everyone in the nested group becomes a member of this one.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {candidates.length === 0 ? (
              <p className="text-sm text-muted-foreground">No other groups to add.</p>
            ) : (
              <>
                <SimpleSelect
                  value={childId}
                  onChange={setChildId}
                  ariaLabel="Group to nest"
                  options={[{ value: "", label: "Choose a group…", disabled: true }, ...candidates.map((g) => ({ value: g.id, label: g.name }))]}
                />
                <Button
                  variant="outline"
                  disabled={busy || !childId}
                  onClick={() =>
                    void run(() => adminApi.addGroupChild(group.id, childId)).then((ok) => {
                      if (ok) setChildId("");
                    })
                  }
                >
                  Add group
                </Button>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
