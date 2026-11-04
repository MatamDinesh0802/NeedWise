"use client";

import { Plus, Trash2, UsersRound } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { EMPTY_NAME_SLUG, NameSlugFields, nameSlugErrors, type NameSlugValue } from "@/components/admin/name-slug-fields";
import { ConfirmDialog } from "@/components/data/confirm-dialog";
import { Field } from "@/components/data/form";
import { EmptyState, ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { adminApi, adminKeys, type Group } from "@/lib/admin/api";
import { errorMessage, fieldErrors } from "@/lib/error-messages";

export function GroupsSection() {
  const { state, refetch } = useApiQuery(adminKeys.groups, adminApi.listGroups);
  const [creating, setCreating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Group | null>(null);

  return (
    <section className="flex flex-col gap-4" aria-labelledby="groups-heading">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id="groups-heading" className="flex items-center gap-2 text-lg font-semibold">
            Groups
            <RefreshingHint active={state.status !== "loading" && state.refreshing} />
          </h3>
          <p className="text-sm text-muted-foreground">
            Groups can contain people and other groups. Share knowledge with a group to reach all of them.
          </p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <Plus data-icon="inline-start" />
          New group
        </Button>
      </div>

      {state.status === "loading" ? (
        <TableSkeleton />
      ) : state.status === "error" ? (
        <ErrorAlert error={state.error} onRetry={() => void refetch()} />
      ) : state.data.items.length === 0 ? (
        <EmptyState icon={UsersRound} title="No groups yet" description="Create a group, then add people or other groups to it." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Slug</TableHead>
              <TableHead>Description</TableHead>
              <TableHead>Source</TableHead>
              <TableHead className="text-right">Members</TableHead>
              <TableHead className="w-10">
                <span className="sr-only">Delete</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {state.data.items.map((group) => (
              <TableRow key={group.id}>
                <TableCell className="font-medium">
                  <Link href={`/admin/groups/${group.id}`} className="hover:underline">
                    {group.name}
                  </Link>
                </TableCell>
                <TableCell className="font-mono text-xs text-muted-foreground">{group.slug}</TableCell>
                <TableCell className="max-w-64 truncate text-muted-foreground" title={group.description ?? undefined}>
                  {group.description ?? "—"}
                </TableCell>
                <TableCell>
                  <Badge variant="outline">{group.source}</Badge>
                </TableCell>
                <TableCell className="text-right tabular-nums">{group.member_count}</TableCell>
                <TableCell>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Delete ${group.name}`}
                    title="Delete group"
                    onClick={() => setDeleteTarget(group)}
                  >
                    <Trash2 />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent className="sm:max-w-md">
          {creating ? <CreateGroupForm onDone={() => setCreating(false)} /> : null}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title={`Delete ${deleteTarget?.name ?? "group"}?`}
        description="Members lose access to knowledge shared only with this group, and groups that contain it lose those members. This can't be undone."
        confirmLabel="Delete group"
        onConfirm={async () => {
          if (!deleteTarget) return;
          await adminApi.deleteGroup(deleteTarget.id);
          await Promise.all([invalidateQueries(adminKeys.groups), invalidateQueries(adminKeys.users)]);
        }}
      />
    </section>
  );
}

function CreateGroupForm({ onDone }: { onDone: () => void }) {
  const [value, setValue] = useState<NameSlugValue>(EMPTY_NAME_SLUG);
  const [description, setDescription] = useState("");
  const [showErrors, setShowErrors] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const code = errorMessage(error).code;
  const serverSlugError =
    code === "slug_taken"
      ? "This slug is already in use."
      : (fieldErrors(error).find((f) => f.field === "slug")?.message ?? null);

  async function submit() {
    setShowErrors(true);
    const errors = nameSlugErrors(value);
    if (errors.name || errors.slug) return;
    setPending(true);
    setError(null);
    try {
      await adminApi.createGroup({
        name: value.name.trim(),
        slug: value.slug,
        description: description.trim() || null,
      });
      await invalidateQueries(adminKeys.groups);
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      className="contents"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <DialogHeader>
        <DialogTitle>New group</DialogTitle>
        <DialogDescription>Add people and nested groups after creating it.</DialogDescription>
      </DialogHeader>
      <div className="flex flex-col gap-3">
        <NameSlugFields
          value={value}
          onChange={(next) => {
            setValue(next);
            if (code === "slug_taken" && next.slug !== value.slug) setError(null);
          }}
          serverSlugError={serverSlugError}
          showErrors={showErrors}
        />
        <Field label="Description (optional)">
          {({ id }) => (
            <Textarea id={id} maxLength={1000} rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
          )}
        </Field>
      </div>
      {error && !serverSlugError ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? "Creating…" : "Create group"}
        </Button>
      </DialogFooter>
    </form>
  );
}
