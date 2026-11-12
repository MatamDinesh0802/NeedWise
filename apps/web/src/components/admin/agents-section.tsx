"use client";

import { Bot, Info, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { ConfirmDialog } from "@/components/data/confirm-dialog";
import { Field, SimpleSelect } from "@/components/data/form";
import { EmptyState, ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { ScopeBadge } from "@/components/knowledge/badges";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import {
  AGENT_LIMITS,
  AGENT_SAVED_TOAST,
  agentsApi,
  agentsKey,
  deleteAgentText,
  emptyAgentForm,
  formFromAgent,
  toAgentFields,
  toCreatePayload,
  toggledPayload,
  validateAgentForm,
  withName,
  type Agent,
  type AgentForm,
} from "@/lib/admin/agents";
import { errorMessage, fieldErrors } from "@/lib/error-messages";
import { knowledgeApi, knowledgeKeys, type AdminCollection } from "@/lib/knowledge/api";
import { formatRelative } from "@/lib/format";
import { toast } from "@/lib/stores/toasts";
import { cn } from "@/lib/utils";

export function AgentsSection() {
  const agents = useApiQuery(agentsKey, agentsApi.list);
  const collections = useApiQuery(knowledgeKeys.adminCollections, knowledgeApi.listAllCollections);
  const [editing, setEditing] = useState<Agent | "new" | null>(null);
  const [toggling, setToggling] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<Agent | null>(null);
  const confirmText = deleting ? deleteAgentText(deleting) : null;

  const byKey = new Map<string, AdminCollection>(
    collections.state.status === "success" ? collections.state.data.items.map((c) => [c.key, c]) : [],
  );

  async function toggle(agent: Agent, enabled: boolean) {
    setToggling(agent.key);
    try {
      await agentsApi.update(agent.key, toggledPayload(agent, enabled));
      await invalidateQueries(agentsKey);
      toast({ title: `${agent.name} ${enabled ? "enabled" : "disabled"}`, description: AGENT_SAVED_TOAST });
    } catch (error) {
      const message = errorMessage(error);
      toast({ title: message.title, description: message.description, tone: "error" });
    } finally {
      setToggling(null);
    }
  }

  return (
    <section className="flex flex-col gap-4" aria-labelledby="agents-heading">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id="agents-heading" className="flex items-center gap-2 text-lg font-semibold">
            Agents
            <RefreshingHint active={agents.state.status === "success" && agents.state.refreshing} />
          </h3>
          <p className="text-sm text-muted-foreground">
            The planner sends parts of a question to these department agents and combines their findings.
          </p>
        </div>
        <Button onClick={() => setEditing("new")}>
          <Plus data-icon="inline-start" />
          New agent
        </Button>
      </div>

      <Alert>
        <Info aria-hidden="true" />
        <AlertDescription>
          Agents only narrow what someone can see: an agent searches its collections as the person asking, never with more
          access.
        </AlertDescription>
      </Alert>

      {agents.state.status === "loading" ? (
        <TableSkeleton />
      ) : agents.state.status === "error" ? (
        <ErrorAlert error={agents.state.error} onRetry={() => void agents.refetch()} />
      ) : agents.state.data.items.length === 0 ? (
        <EmptyState icon={Bot} title="No agents yet" description="Without agents, every question is answered by one search across everything the person can see." />
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {agents.state.data.items.map((agent) => (
            <li key={agent.id} className={cn("flex flex-col gap-2 rounded-xl border bg-card p-4", !agent.enabled && "opacity-70")}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-medium">{agent.name}</p>
                  <p className="font-mono text-xs text-muted-foreground">{agent.key}</p>
                </div>
                <label className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                  {agent.enabled ? "Enabled" : "Disabled"}
                  <Switch
                    checked={agent.enabled}
                    disabled={toggling === agent.key}
                    onCheckedChange={(checked) => void toggle(agent, checked)}
                    aria-label={`${agent.enabled ? "Disable" : "Enable"} ${agent.name}`}
                  />
                </label>
              </div>
              <p className="line-clamp-2 text-sm text-muted-foreground" title={agent.description}>
                {agent.description}
              </p>
              <div className="flex flex-wrap gap-1">
                {agent.collections.map((key) => (
                  <Badge key={key} variant="secondary" title={byKey.get(key)?.description || key}>
                    {byKey.get(key)?.name ?? key}
                  </Badge>
                ))}
              </div>
              <div className="mt-auto flex items-center justify-between gap-2 pt-1 text-xs text-muted-foreground">
                <span>
                  {agent.examples.length} {agent.examples.length === 1 ? "example" : "examples"} · up to {agent.max_searches}{" "}
                  {agent.max_searches === 1 ? "search" : "searches"} · updated {formatRelative(agent.updated_at)}
                </span>
                <div className="flex shrink-0 gap-1">
                  <Button variant="ghost" size="sm" aria-label={`Delete ${agent.name}`} onClick={() => setDeleting(agent)}>
                    <Trash2 data-icon="inline-start" aria-hidden="true" />
                    Delete
                  </Button>
                  <Button variant="outline" size="sm" aria-label={`Edit ${agent.name}`} onClick={() => setEditing(agent)}>
                    Edit
                  </Button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      <Dialog open={editing !== null} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
          {editing !== null ? (
            <AgentFormView
              key={editing === "new" ? "new" : editing.key}
              agent={editing === "new" ? null : editing}
              collections={collections.state.status === "success" ? collections.state.data.items : []}
              collectionsError={collections.state.status === "error" ? collections.state.error : null}
              onDone={() => setEditing(null)}
            />
          ) : null}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={confirmText?.title ?? "Delete agent?"}
        description={confirmText?.description ?? ""}
        confirmLabel={confirmText?.confirm ?? "Delete agent"}
        onConfirm={async () => {
          if (!deleting) return;
          await agentsApi.remove(deleting.key);
          await invalidateQueries(agentsKey);
          toast({ title: `${deleting.name} deleted`, description: "The planner no longer sends questions to it." });
        }}
      />
    </section>
  );
}

function AgentFormView({
  agent,
  collections,
  collectionsError,
  onDone,
}: {
  agent: Agent | null;
  collections: AdminCollection[];
  collectionsError: unknown;
  onDone: () => void;
}) {
  const creating = agent === null;
  const [form, setForm] = useState<AgentForm>(() => (agent ? formFromAgent(agent) : emptyAgentForm()));
  const [showErrors, setShowErrors] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const errors = validateAgentForm(form, { creating });
  const server = Object.fromEntries(fieldErrors(error).map((f) => [f.field, f.message]));
  const code = errorMessage(error).code;
  const keyError = code === "slug_taken" ? "An agent with this key already exists." : (server.key ?? null);
  const shown = (field: keyof typeof errors) => (showErrors ? (errors[field] ?? null) : null);
  const patch = (change: Partial<AgentForm>) => setForm((f) => ({ ...f, ...change }));

  async function save() {
    setShowErrors(true);
    if (Object.keys(errors).length > 0) return;
    setPending(true);
    setError(null);
    try {
      const saved = creating ? await agentsApi.create(toCreatePayload(form)) : await agentsApi.update(form.key, toAgentFields(form));
      await invalidateQueries(agentsKey);
      toast({ title: creating ? `${saved.name} created` : `${saved.name} saved`, description: AGENT_SAVED_TOAST });
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
        <DialogTitle>{creating ? "New agent" : `Edit ${agent.name}`}</DialogTitle>
        <DialogDescription>Changes take effect on the next question; no deploy needed.</DialogDescription>
      </DialogHeader>

      <div className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name" error={shown("name")}>
            {({ id, describedBy, invalid }) => (
              <Input
                id={id}
                maxLength={AGENT_LIMITS.nameMax}
                value={form.name}
                onChange={(e) => setForm((f) => withName(f, e.target.value))}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
              />
            )}
          </Field>
          <Field
            label="Key"
            hint={creating ? "Used to route questions to this agent. Can't be changed later." : "Can't be changed."}
            error={creating ? (keyError ?? (showErrors || form.keyEdited ? (errors.key ?? null) : null)) : null}
          >
            {({ id, describedBy, invalid }) => (
              <Input
                id={id}
                className="font-mono"
                maxLength={AGENT_LIMITS.keyMax}
                spellCheck={false}
                autoCapitalize="none"
                value={form.key}
                readOnly={!creating}
                disabled={!creating}
                onChange={(e) => patch({ key: e.target.value, keyEdited: true })}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
              />
            )}
          </Field>
        </div>

        <Field label="Description" hint="Written for the planner: what can this agent answer?" error={shown("description") ?? server.description ?? null}>
          {({ id, describedBy, invalid }) => (
            <Textarea
              id={id}
              rows={3}
              maxLength={AGENT_LIMITS.descriptionMax}
              value={form.description}
              onChange={(e) => patch({ description: e.target.value })}
              aria-describedby={describedBy}
              aria-invalid={invalid || undefined}
            />
          )}
        </Field>

        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-sm font-medium">Collections it searches</legend>
          {collectionsError ? <ErrorAlert error={collectionsError} /> : null}
          <ul className="grid max-h-56 gap-1 overflow-y-auto rounded-lg border p-2 sm:grid-cols-2">
            {collections.map((c) => {
              const id = `agent-collection-${c.key}`;
              const checked = form.collections.includes(c.key);
              return (
                <li key={c.key} className="flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-muted/50">
                  <Checkbox
                    id={id}
                    checked={checked}
                    onCheckedChange={(next) =>
                      patch({
                        collections: next ? [...form.collections, c.key] : form.collections.filter((k) => k !== c.key),
                      })
                    }
                  />
                  <label htmlFor={id} className="min-w-0 flex-1 truncate text-sm" title={c.description}>
                    {c.name}
                  </label>
                  <ScopeBadge scope={c.scope} />
                </li>
              );
            })}
          </ul>
          {shown("collections") ? <p className="text-xs text-destructive">{errors.collections}</p> : null}
          {code === "unknown_collection" ? <p className="text-xs text-destructive">{errorMessage(error).description}</p> : null}
        </fieldset>

        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-sm font-medium">Example questions</legend>
          <p className="-mt-1 text-xs text-muted-foreground">
            Questions this agent should get. Close matches are routed to it instantly, without asking the planner.
          </p>
          <ul className="flex flex-col gap-2">
            {form.examples.map((example, index) => (
              <li key={index} className="flex gap-2">
                <Input
                  value={example}
                  maxLength={AGENT_LIMITS.exampleMax}
                  aria-label={`Example question ${index + 1}`}
                  placeholder="e.g. How do I restart the fleet controller?"
                  onChange={(e) => patch({ examples: form.examples.map((x, i) => (i === index ? e.target.value : x)) })}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`Remove example ${index + 1}`}
                  onClick={() => patch({ examples: form.examples.length > 1 ? form.examples.filter((_, i) => i !== index) : [""] })}
                >
                  <Trash2 />
                </Button>
              </li>
            ))}
          </ul>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="w-fit"
            disabled={form.examples.length >= AGENT_LIMITS.examplesMax}
            onClick={() => patch({ examples: [...form.examples, ""] })}
          >
            <Plus data-icon="inline-start" />
            Add example
          </Button>
          {shown("examples") ? <p className="text-xs text-destructive">{errors.examples}</p> : null}
        </fieldset>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Searches per question" hint="More searches find more, but take longer.">
            {({ id }) => (
              <SimpleSelect
                id={id}
                value={String(form.maxSearches)}
                onChange={(v) => patch({ maxSearches: Number(v) })}
                options={[1, 2, 3, 4, 5].map((n) => ({ value: String(n), label: n === 1 ? "1 search" : `Up to ${n} searches` }))}
              />
            )}
          </Field>
          <div className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2">
            <div>
              <p className="text-sm font-medium">Enabled</p>
              <p className="text-xs text-muted-foreground">Disabled agents are never called.</p>
            </div>
            <Switch checked={form.enabled} onCheckedChange={(checked) => patch({ enabled: checked })} aria-label="Enabled" />
          </div>
        </div>

        <Field label="Instructions (optional)" hint="Extra guidance for how this agent should read and report its findings." error={shown("instructions")}>
          {({ id, describedBy }) => (
            <Textarea
              id={id}
              rows={3}
              maxLength={AGENT_LIMITS.instructionsMax}
              value={form.instructions}
              onChange={(e) => patch({ instructions: e.target.value })}
              aria-describedby={describedBy}
            />
          )}
        </Field>
      </div>

      {error && !keyError && code !== "unknown_collection" ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? "Saving…" : creating ? "Create agent" : "Save agent"}
        </Button>
      </DialogFooter>
    </form>
  );
}
