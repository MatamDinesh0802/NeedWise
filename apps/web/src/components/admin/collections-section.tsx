"use client";

import { Info, LibraryBig, Plus } from "lucide-react";
import { useState } from "react";

import { EMPTY_NAME_SLUG, NameSlugFields, nameSlugErrors, type NameSlugValue } from "@/components/admin/name-slug-fields";
import { PersonPicker } from "@/components/admin/person-picker";
import { SharingEditor } from "@/components/admin/sharing-editor";
import { Field, SimpleSelect } from "@/components/data/form";
import { EmptyState, ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { ScopeBadge } from "@/components/knowledge/badges";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
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
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { useDirectory } from "@/hooks/use-directory";
import { useSession } from "@/hooks/use-session";
import { errorMessage, fieldErrors } from "@/lib/error-messages";
import {
  knowledgeApi,
  knowledgeKeys,
  type AdminCollection,
  type CollectionCreate,
  type Sensitivity,
  type Trust,
} from "@/lib/knowledge/api";
import { SCOPE_LABELS, SENSITIVITY_LABELS, TRUST_LABELS } from "@/lib/knowledge/labels";
import { intervalError, intervalText, parseInterval } from "@/lib/knowledge/review-interval";
import {
  buildSharingPayload,
  grantsToEntries,
  principalLabel,
  SCOPE_DESCRIPTIONS,
  scopeDefaultViewer,
  sharingProblems,
  sortPrincipals,
  type Directory,
  type ShareEntry,
} from "@/lib/knowledge/principals";
import type { Me } from "@/lib/session";

type DirectoryData = ReturnType<typeof useDirectory>;
type CreatableScope = CollectionCreate["scope"];

const SCOPES: CreatableScope[] = ["enterprise", "department", "restricted"];

export function CollectionsSection() {
  const { state } = useSession();
  if (state.kind !== "signed_in") return null; // AdminShell handles the other states
  return <Collections me={state.me} />;
}

function Collections({ me }: { me: Me }) {
  const { state, refetch } = useApiQuery(knowledgeKeys.adminCollections, knowledgeApi.listAllCollections);
  const dir = useDirectory(me.tenant);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<AdminCollection | null>(null);
  const [governing, setGoverning] = useState<AdminCollection | null>(null);

  return (
    <section className="flex flex-col gap-4" aria-labelledby="collections-heading">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id="collections-heading" className="flex items-center gap-2 text-lg font-semibold">
            Collections
            <RefreshingHint active={state.status !== "loading" && state.refreshing} />
          </h3>
          <p className="text-sm text-muted-foreground">Who can view and edit each knowledge collection.</p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <Plus data-icon="inline-start" />
          New collection
        </Button>
      </div>

      <Alert>
        <Info aria-hidden="true" />
        <AlertTitle>Managing sharing isn&apos;t reading</AlertTitle>
        <AlertDescription>
          As an administrator you decide who can open each collection, but you still can&apos;t read restricted
          documents unless you are given access yourself.
        </AlertDescription>
      </Alert>

      <ul className="grid gap-1 text-sm text-muted-foreground sm:grid-cols-3">
        {SCOPES.map((scope) => (
          <li key={scope} className="flex items-start gap-2">
            <ScopeBadge scope={scope} />
            <span>{SCOPE_DESCRIPTIONS[scope]}</span>
          </li>
        ))}
      </ul>

      {dir.error ? <ErrorAlert error={dir.error} /> : null}

      {state.status === "loading" ? (
        <TableSkeleton />
      ) : state.status === "error" ? (
        <ErrorAlert error={state.error} onRetry={() => void refetch()} />
      ) : state.data.items.length === 0 ? (
        <EmptyState icon={LibraryBig} title="No collections yet" description="Create the first collection to start adding knowledge." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Collection</TableHead>
              <TableHead>Scope</TableHead>
              <TableHead className="text-right">Docs</TableHead>
              <TableHead>Can view</TableHead>
              <TableHead>Can edit</TableHead>
              <TableHead>Owner and reviews</TableHead>
              <TableHead className="w-28">
                <span className="sr-only">Actions</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {state.data.items.map((c) => {
              const entries = grantsToEntries(c.grants);
              const viewers = entries.filter((e) => e.level === "view").map((e) => e.principal);
              const editors = entries.filter((e) => e.level === "edit").map((e) => e.principal);
              return (
                <TableRow key={c.id} className="align-top">
                  <TableCell className="max-w-64">
                    <p className="font-medium">{c.name}</p>
                    <p className="font-mono text-xs text-muted-foreground">{c.key}</p>
                    {c.scope === "department" && c.department_id ? (
                      <p className="text-xs text-muted-foreground">
                        {dir.directory.departments.get(c.department_id)?.name ?? "Unknown department"}
                      </p>
                    ) : null}
                  </TableCell>
                  <TableCell>
                    <ScopeBadge scope={c.scope} />
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{c.document_count}</TableCell>
                  <TableCell className="max-w-72">
                    <PrincipalList principals={viewers} directory={dir.directory} loading={dir.loading} empty="Only editors" />
                  </TableCell>
                  <TableCell className="max-w-72">
                    <PrincipalList principals={editors} directory={dir.directory} loading={dir.loading} empty="Nobody" />
                  </TableCell>
                  <TableCell className="max-w-48 text-sm">
                    {c.owner ? <p>{c.owner.display_name}</p> : <p className="text-muted-foreground">No owner</p>}
                    <p className="text-xs text-muted-foreground">{intervalText(c.review_interval_days)}</p>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-col items-stretch gap-1.5">
                      <Button variant="outline" size="sm" onClick={() => setEditing(c)}>
                        Edit sharing
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => setGoverning(c)} aria-label={`Owner and reviews of ${c.name}`}>
                        Owner and reviews
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}

      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
          {creating ? <CreateCollectionForm me={me} dir={dir} onDone={() => setCreating(false)} /> : null}
        </DialogContent>
      </Dialog>

      <Dialog open={editing !== null} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
          {editing ? <EditSharingForm key={editing.id} collection={editing} dir={dir} onDone={() => setEditing(null)} /> : null}
        </DialogContent>
      </Dialog>

      <Dialog open={governing !== null} onOpenChange={(open) => !open && setGoverning(null)}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
          {governing ? <EditGovernanceForm key={governing.id} collection={governing} onDone={() => setGoverning(null)} /> : null}
        </DialogContent>
      </Dialog>
    </section>
  );
}

function PrincipalList({
  principals,
  directory,
  loading,
  empty,
}: {
  principals: string[];
  directory: Directory;
  loading: boolean;
  empty: string;
}) {
  if (principals.length === 0) return <span className="text-sm text-muted-foreground">{empty}</span>;
  if (loading) return <span className="text-sm text-muted-foreground">Loading names…</span>;
  return (
    <ul className="flex flex-col gap-0.5 text-sm">
      {sortPrincipals(principals, directory).map((p) => {
        const label = principalLabel(p, directory);
        return (
          <li key={p} className={label.known ? undefined : "text-muted-foreground italic"} title={p}>
            {label.label}
          </li>
        );
      })}
    </ul>
  );
}

async function refreshCollections() {
  await Promise.all([invalidateQueries(knowledgeKeys.adminCollections), invalidateQueries(knowledgeKeys.all)]);
}

function ProblemList({ problems }: { problems: string[] }) {
  if (problems.length === 0) return null;
  return (
    <ul className="list-disc pl-5 text-sm text-destructive">
      {problems.map((p) => (
        <li key={p}>{p}</li>
      ))}
    </ul>
  );
}

function CreateCollectionForm({ me, dir, onDone }: { me: Me; dir: DirectoryData; onDone: () => void }) {
  const [names, setNames] = useState<NameSlugValue>(EMPTY_NAME_SLUG);
  const [description, setDescription] = useState("");
  const [scope, setScope] = useState<CreatableScope>("restricted");
  const [departmentId, setDepartmentId] = useState("");
  const [trust, setTrust] = useState<Trust>("reference");
  const [sensitivity, setSensitivity] = useState<Sensitivity>("internal");
  const [entries, setEntries] = useState<ShareEntry[]>([]);
  const [showErrors, setShowErrors] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const fixedViewer = scopeDefaultViewer(scope, me.tenant.id, departmentId || null);
  const problems = sharingProblems({
    scope,
    departmentId: departmentId || null,
    entries: fixedViewer ? [...entries, { principal: fixedViewer, level: "view" }] : entries,
    creating: true,
  });
  const keyError =
    errorMessage(error).code === "slug_taken"
      ? "A collection with this key already exists."
      : (fieldErrors(error).find((f) => f.field === "key")?.message ?? null);

  async function submit() {
    setShowErrors(true);
    const nameErrors = nameSlugErrors({ ...names });
    if (nameErrors.name || nameErrors.slug || problems.length > 0) return;
    setPending(true);
    setError(null);
    try {
      await knowledgeApi.createCollection({
        key: names.slug,
        name: names.name.trim(),
        description: description.trim(),
        scope,
        department_id: scope === "department" ? departmentId : null,
        default_trust: trust,
        default_sensitivity: sensitivity,
        ...buildSharingPayload(entries),
      });
      await refreshCollections();
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
        <DialogTitle>New collection</DialogTitle>
        <DialogDescription>Documents uploaded to it inherit its sharing and defaults.</DialogDescription>
      </DialogHeader>
      <div className="flex flex-col gap-4">
        <NameSlugFields
          value={names}
          onChange={setNames}
          slugLabel="Key"
          serverSlugError={keyError}
          showErrors={showErrors}
        />
        <Field label="Description (optional)">
          {({ id }) => <Textarea id={id} rows={2} maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} />}
        </Field>
        <Field label="Scope" hint={SCOPE_DESCRIPTIONS[scope]}>
          {({ id }) => (
            <SimpleSelect id={id} value={scope} onChange={setScope} options={SCOPES.map((s) => ({ value: s, label: SCOPE_LABELS[s] }))} />
          )}
        </Field>
        {scope === "department" ? (
          <Field label="Department" error={showErrors && !departmentId ? "Choose the department." : null}>
            {({ id }) => (
              <SimpleSelect
                id={id}
                value={departmentId}
                onChange={setDepartmentId}
                options={[{ value: "", label: "Choose a department…", disabled: true }, ...dir.departments.map((d) => ({ value: d.id, label: d.name }))]}
              />
            )}
          </Field>
        ) : null}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Default trust" hint={TRUST_LABELS[trust].description}>
            {({ id }) => (
              <SimpleSelect
                id={id}
                value={trust}
                onChange={setTrust}
                options={(Object.keys(TRUST_LABELS) as Trust[]).map((t) => ({ value: t, label: TRUST_LABELS[t].label }))}
              />
            )}
          </Field>
          <Field label="Default sensitivity" hint={SENSITIVITY_LABELS[sensitivity].description}>
            {({ id }) => (
              <SimpleSelect
                id={id}
                value={sensitivity}
                onChange={setSensitivity}
                options={(Object.keys(SENSITIVITY_LABELS) as Sensitivity[]).map((s) => ({ value: s, label: SENSITIVITY_LABELS[s].label }))}
              />
            )}
          </Field>
        </div>
        <div className="flex flex-col gap-2">
          <p className="text-sm font-medium">Sharing</p>
          <SharingEditor
            entries={entries}
            onChange={setEntries}
            directory={dir.directory}
            groups={dir.groups}
            departments={dir.departments}
            fixed={fixedViewer ? [fixedViewer] : []}
            disabled={pending}
          />
        </div>
        {showErrors ? <ProblemList problems={problems} /> : null}
      </div>
      {error && !keyError ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? "Creating…" : "Create collection"}
        </Button>
      </DialogFooter>
    </form>
  );
}

function EditSharingForm({ collection, dir, onDone }: { collection: AdminCollection; dir: DirectoryData; onDone: () => void }) {
  const [entries, setEntries] = useState<ShareEntry[]>(() => grantsToEntries(collection.grants));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const problems = sharingProblems({ scope: collection.scope, departmentId: collection.department_id, entries, creating: false });

  // Removing the scope's own default viewer is allowed but usually a mistake: warn.
  const expected =
    collection.scope === "enterprise" || collection.scope === "department"
      ? scopeDefaultViewer(collection.scope, dir.directory.tenant.id, collection.department_id)
      : null;
  const lostDefault = expected !== null && !entries.some((e) => e.principal === expected);

  async function save() {
    if (problems.length > 0) return;
    setPending(true);
    setError(null);
    try {
      await knowledgeApi.replaceGrants(collection.id, buildSharingPayload(entries));
      await refreshCollections();
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
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <DialogHeader>
        <DialogTitle>Sharing: {collection.name}</DialogTitle>
        <DialogDescription>
          {SCOPE_LABELS[collection.scope]} collection. {SCOPE_DESCRIPTIONS[collection.scope]} Editors can also upload and
          delete documents.
        </DialogDescription>
      </DialogHeader>
      <SharingEditor
        entries={entries}
        onChange={setEntries}
        directory={dir.directory}
        groups={dir.groups}
        departments={dir.departments}
        disabled={pending}
      />
      {lostDefault && expected ? (
        <p className="text-sm text-amber-700 dark:text-amber-400" role="status">
          Without “{principalLabel(expected, dir.directory).label}”, this {SCOPE_LABELS[collection.scope].toLowerCase()} collection
          is only visible to the people listed above.
        </p>
      ) : null}
      <ProblemList problems={problems} />
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending || problems.length > 0}>
          {pending ? "Saving…" : "Save sharing"}
        </Button>
      </DialogFooter>
    </form>
  );
}

/** The collection's owner (approves official status, sees its governance overview) and review interval. */
export function EditGovernanceForm({ collection, onDone }: { collection: AdminCollection; onDone: () => void }) {
  const [owner, setOwner] = useState(collection.owner);
  const [days, setDays] = useState(collection.review_interval_days?.toString() ?? "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const problem = intervalError(days);

  async function save() {
    if (problem) return;
    setPending(true);
    setError(null);
    try {
      await knowledgeApi.updateGovernance(collection.id, { owner_id: owner?.id ?? null, review_interval_days: parseInterval(days) });
      await refreshCollections();
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
        void save();
      }}
    >
      <DialogHeader>
        <DialogTitle>Owner and reviews: {collection.name}</DialogTitle>
        <DialogDescription>
          The owner approves requests to make documents official and sees this collection&apos;s governance overview. The
          owner can also view and edit the collection.
        </DialogDescription>
      </DialogHeader>
      <div className="flex flex-col gap-2">
        <p className="text-sm font-medium">Owner</p>
        {owner ? (
          <div className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm">
            <span>{owner.display_name}</span>
            <Button type="button" variant="ghost" size="sm" onClick={() => setOwner(null)} disabled={pending}>
              Remove
            </Button>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">No owner: administrators approve official status.</p>
        )}
        <PersonPicker
          label="Find the owner"
          exclude={new Set(owner ? [owner.id] : [])}
          disabled={pending}
          onPick={(user) => setOwner({ id: user.id, display_name: user.display_name })}
        />
      </div>
      <Field
        label="Review every (days)"
        hint="Each document is due for review this many days after its last review. Leave empty for no reviews. Changing it reschedules every document."
        error={problem}
      >
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            inputMode="numeric"
            value={days}
            onChange={(e) => setDays(e.target.value)}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            disabled={pending}
            placeholder="e.g. 365"
            className="max-w-40"
          />
        )}
      </Field>
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending || problem !== null}>
          {pending ? "Saving…" : "Save"}
        </Button>
      </DialogFooter>
    </form>
  );
}
