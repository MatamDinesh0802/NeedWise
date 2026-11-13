"use client";

import { Link2, Trash2, UserRoundX } from "lucide-react";
import { useState } from "react";

import { PersonPicker } from "@/components/admin/person-picker";
import { Field, SimpleSelect, type SelectOption } from "@/components/data/form";
import { ErrorAlert, TableSkeleton } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { useSession } from "@/hooks/use-session";
import { adminApi, adminKeys, type AdminUser, type Department, type Group } from "@/lib/admin/api";
import {
  describeExternal,
  identityMappingsApi,
  identityMappingsKeys,
  isExternal,
  normalizeExternal,
  type IdentityMappings,
} from "@/lib/admin/identity-mappings";
import { errorMessage } from "@/lib/error-messages";
import { toast } from "@/lib/stores/toasts";

type TargetKind = "user" | "group" | "dept" | "dept-manager" | "org";

const TARGET_KINDS: SelectOption<TargetKind>[] = [
  { value: "group", label: "A group" },
  { value: "dept", label: "A department's members" },
  { value: "dept-manager", label: "A department's managers" },
  { value: "user", label: "A person" },
  { value: "org", label: "Everyone in the organization" },
];

/**
 * Admin > Sources: source principals permission sync could not match to anyone (they grant
 * nothing), and the mappings that match them (docs/permission-sync.md).
 */
export function IdentityMappingsSection() {
  const { state: session } = useSession();
  const mappings = useApiQuery(identityMappingsKeys.all, identityMappingsApi.list);
  const groups = useApiQuery(adminKeys.groups, adminApi.listGroups);
  const departments = useApiQuery(adminKeys.departments, adminApi.listDepartments);
  const [busy, setBusy] = useState(false);
  const tenantId = session.kind === "signed_in" ? session.me.tenant.id : null;

  async function run(action: () => Promise<string>) {
    setBusy(true);
    try {
      const done = await action();
      await invalidateQueries(identityMappingsKeys.all);
      toast({ title: done });
    } catch (error) {
      const message = errorMessage(error);
      toast({ title: message.title, description: message.description, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col gap-3 rounded-xl border p-4" aria-labelledby="identity-heading">
      <div>
        <h4 id="identity-heading" className="font-medium">
          Source accounts
        </h4>
        <p className="text-xs text-muted-foreground">
          With permission sync, each synced document keeps its source&apos;s sharing. People are matched by e-mail; a group, a domain or a
          Slack or GitHub account gives nobody access until you map it here. Changes apply at once.
        </p>
      </div>
      {mappings.state.status === "loading" ? (
        <TableSkeleton rows={2} />
      ) : mappings.state.status === "error" ? (
        <ErrorAlert error={mappings.state.error} onRetry={() => void mappings.refetch()} />
      ) : (
        <IdentityMappingsView
          data={mappings.state.data}
          groups={groups.state.status === "success" ? groups.state.data.items : []}
          departments={departments.state.status === "success" ? departments.state.data.items : []}
          tenantId={tenantId}
          busy={busy}
          onMap={(external, principal) =>
            void run(async () => {
              const saved = await identityMappingsApi.save(external, principal);
              const count = saved.documents_changed;
              return `Mapped ${saved.mapping.external}: ${count} document${count === 1 ? "" : "s"} updated`;
            })
          }
          onRemove={(mappingId, external) =>
            void run(async () => {
              await identityMappingsApi.remove(mappingId);
              return `Mapping for ${external} removed`;
            })
          }
        />
      )}
    </section>
  );
}

export function IdentityMappingsView({
  data,
  groups,
  departments,
  tenantId,
  busy,
  onMap,
  onRemove,
  initialExternal = "",
}: {
  data: IdentityMappings;
  groups: Group[];
  departments: Department[];
  tenantId: string | null;
  busy: boolean;
  onMap: (external: string, principal: string) => void;
  onRemove: (mappingId: string, external: string) => void;
  initialExternal?: string;
}) {
  const [external, setExternal] = useState(initialExternal);

  return (
    <div className="flex flex-col gap-4">
      {data.unmapped.length === 0 ? (
        <p className="text-sm text-muted-foreground">Every account the sources name is matched to someone.</p>
      ) : (
        <div className="flex flex-col gap-2">
          <p className="text-sm font-medium">Not matched: they give nobody access</p>
          <ul className="flex flex-col divide-y rounded-lg border" aria-label="Unmatched source accounts">
            {data.unmapped.map((u) => {
              const described = describeExternal(u.external);
              return (
                <li key={u.external} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
                  <span className="flex min-w-0 items-center gap-2">
                    <UserRoundX className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                    <span className="min-w-0">
                      <span className="block truncate font-mono">{described.value}</span>
                      <span className="block text-xs text-muted-foreground">
                        {described.kind} · named on {u.documents} document{u.documents === 1 ? "" : "s"}
                        {u.sources.length ? ` from ${u.sources.join(", ")}` : ""}
                      </span>
                    </span>
                  </span>
                  <Button size="sm" variant="outline" disabled={busy} onClick={() => setExternal(u.external)}>
                    <Link2 aria-hidden="true" /> Map
                  </Button>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      <MapForm
        key={external}
        external={external}
        groups={groups}
        departments={departments}
        tenantId={tenantId}
        busy={busy}
        onMap={(value, principal) => {
          onMap(value, principal);
          setExternal("");
        }}
      />

      {data.mappings.length ? (
        <div className="flex flex-col gap-2">
          <p className="text-sm font-medium">Mapped</p>
          <ul className="flex flex-col divide-y rounded-lg border" aria-label="Mapped source accounts">
            {data.mappings.map((m) => (
              <li key={m.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
                <span className="min-w-0">
                  <span className="block truncate font-mono">{m.external}</span>
                  <span className="block text-xs text-muted-foreground">
                    is {m.principal_name ?? <Badge variant="secondary">no longer exists: grants nothing</Badge>}
                  </span>
                </span>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => onRemove(m.id, m.external)} aria-label={`Remove the mapping for ${m.external}`}>
                  <Trash2 aria-hidden="true" />
                </Button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function MapForm({
  external: initial,
  groups,
  departments,
  tenantId,
  busy,
  onMap,
}: {
  external: string;
  groups: Group[];
  departments: Department[];
  tenantId: string | null;
  busy: boolean;
  onMap: (external: string, principal: string) => void;
}) {
  const [external, setExternal] = useState(initial);
  const [kind, setKind] = useState<TargetKind>("group");
  const [target, setTarget] = useState("");
  const [person, setPerson] = useState<AdminUser | null>(null);
  const valid = isExternal(external);
  const options: SelectOption<string>[] =
    kind === "group"
      ? groups.map((g) => ({ value: g.id, label: g.name }))
      : kind === "dept" || kind === "dept-manager"
        ? departments.map((d) => ({ value: d.id, label: d.name }))
        : [];
  const principal =
    kind === "org" ? (tenantId ? `org:${tenantId}` : null) : kind === "user" ? (person ? `user:${person.id}` : null) : target ? `${kind}:${target}` : null;

  return (
    <form
      className="flex flex-col gap-3 rounded-lg border border-dashed p-3"
      aria-label="Map a source account"
      onSubmit={(event) => {
        event.preventDefault();
        if (valid && principal) onMap(normalizeExternal(external), principal);
      }}
    >
      <Field
        label="Source account"
        hint="As listed above, e.g. group:eng-leads@acme.test or github-user:octocat"
        error={external && !valid ? "Not a source account: kind:value, such as group:… or github-user:…" : null}
      >
        {(ids) => (
          <Input
            id={ids.id}
            aria-describedby={ids.describedBy}
            aria-invalid={ids.invalid}
            value={external}
            onChange={(e) => setExternal(e.target.value)}
            disabled={busy}
            className="font-mono"
          />
        )}
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Is">
          {(ids) => (
            <SimpleSelect
              id={ids.id}
              value={kind}
              onChange={(value) => {
                setKind(value);
                setTarget("");
                setPerson(null);
              }}
              options={TARGET_KINDS}
              disabled={busy}
            />
          )}
        </Field>
        {kind === "group" || kind === "dept" || kind === "dept-manager" ? (
          <Field label={kind === "group" ? "Group" : "Department"}>
            {(ids) => (
              <SimpleSelect
                id={ids.id}
                value={target}
                onChange={setTarget}
                options={[{ value: "", label: "Choose…", disabled: true }, ...options]}
                disabled={busy}
              />
            )}
          </Field>
        ) : null}
      </div>
      {kind === "user" ? (
        person ? (
          <p className="text-sm">
            {person.display_name} <span className="text-muted-foreground">({person.email})</span>{" "}
            <Button type="button" size="sm" variant="link" onClick={() => setPerson(null)}>
              Change
            </Button>
          </p>
        ) : (
          <PersonPicker onPick={setPerson} exclude={new Set()} disabled={busy} />
        )
      ) : null}
      <div>
        <Button type="submit" size="sm" disabled={busy || !valid || !principal}>
          Save mapping
        </Button>
      </div>
    </form>
  );
}
