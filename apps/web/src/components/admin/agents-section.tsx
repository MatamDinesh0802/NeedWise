"use client";

import { Bot, Info, Plus, Trash2 } from "lucide-react";
import { useRef, useState } from "react";

import { AgentTestConsole } from "@/components/admin/agent-test-console";
import { AgentToolsPicker } from "@/components/admin/agent-tools-picker";
import { AgentVersionsPanel } from "@/components/admin/agent-versions";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import {
  AGENT_LIMITS,
  AGENT_SAVED_TOAST,
  agentsApi,
  agentsKey,
  agentToolsKey,
  agentVersionsKey,
  DEFAULT_MODEL,
  deleteAgentText,
  emptyAgentForm,
  fetchModelCatalog,
  formFromAgent,
  modelCatalogKey,
  modelLabel,
  modelOptions,
  OUTPUT_TOKEN_CHOICES,
  toCreatePayload,
  toggledPayload,
  toUpdatePayload,
  validateAgentForm,
  withName,
  withTools,
  type Agent,
  type AgentForm,
  type AgentTool,
  type ModelCatalog,
} from "@/lib/admin/agents";
import { errorMessage, fieldErrors } from "@/lib/error-messages";
import { knowledgeApi, knowledgeKeys, type AdminCollection } from "@/lib/knowledge/api";
import { formatRelative } from "@/lib/format";
import { toast } from "@/lib/stores/toasts";
import { cn } from "@/lib/utils";

export function AgentsSection() {
  const agents = useApiQuery(agentsKey, agentsApi.list);
  const collections = useApiQuery(knowledgeKeys.adminCollections, knowledgeApi.listAllCollections);
  const catalog = useApiQuery(modelCatalogKey, fetchModelCatalog);
  const [editing, setEditing] = useState<Agent | "new" | null>(null);
  const [toggling, setToggling] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<Agent | null>(null);
  const confirmText = deleting ? deleteAgentText(deleting) : null;
  const models = catalog.state.status === "success" ? catalog.state.data : null;

  const byKey = new Map<string, AdminCollection>(
    collections.state.status === "success" ? collections.state.data.items.map((c) => [c.key, c]) : [],
  );

  async function toggle(agent: Agent, enabled: boolean) {
    setToggling(agent.key);
    try {
      await agentsApi.update(agent.key, toggledPayload(agent, enabled));
      await invalidateQueries(agentsKey);
      await invalidateQueries(agentVersionsKey(agent.key));
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
          Agents only narrow what someone can see: an agent searches its collections and calls its tools as the person
          asking, never with more access.
        </AlertDescription>
      </Alert>

      {agents.state.status === "loading" ? (
        <TableSkeleton />
      ) : agents.state.status === "error" ? (
        <ErrorAlert error={agents.state.error} onRetry={() => void agents.refetch()} />
      ) : agents.state.data.items.length === 0 ? (
        <EmptyState
          icon={Bot}
          title="No agents yet"
          description="Without agents, every question is answered by one search across everything the person can see."
        />
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {agents.state.data.items.map((agent) => (
            <li key={agent.id} className={cn("flex flex-col gap-2 rounded-xl border bg-card p-4", !agent.enabled && "opacity-70")}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-medium">{agent.name}</p>
                  <p className="font-mono text-xs text-muted-foreground">
                    {agent.key} · v{agent.version}
                  </p>
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
                {agent.tools.map((tool) => (
                  <Badge key={tool} variant="outline" className="font-mono" title="Tool">
                    {tool}
                  </Badge>
                ))}
              </div>
              <div className="mt-auto flex items-center justify-between gap-2 pt-1 text-xs text-muted-foreground">
                <span>
                  {agent.examples.length} {agent.examples.length === 1 ? "example" : "examples"} · up to {agent.max_searches}{" "}
                  {agent.max_searches === 1 ? "search" : "searches"}
                  {agent.tools.length ? ` · ${agent.max_tool_calls} tool ${agent.max_tool_calls === 1 ? "call" : "calls"}` : ""} ·{" "}
                  {modelLabel(models, agent.model)} · updated {formatRelative(agent.updated_at)}
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
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
          {editing !== null ? (
            <AgentEditor
              key={editing === "new" ? "new" : `${editing.key}-${editing.version}`}
              agent={editing === "new" ? null : editing}
              collections={collections.state.status === "success" ? collections.state.data.items : []}
              collectionsError={collections.state.status === "error" ? collections.state.error : null}
              catalog={models}
              catalogFailed={catalog.state.status === "error"}
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

type EditorTab = "definition" | "test" | "versions";

function AgentEditor({
  agent,
  collections,
  collectionsError,
  catalog,
  catalogFailed,
  onDone,
}: {
  agent: Agent | null;
  collections: AdminCollection[];
  collectionsError: unknown;
  catalog: ModelCatalog | null;
  catalogFailed: boolean;
  onDone: () => void;
}) {
  const creating = agent === null;
  const tools = useApiQuery(agentToolsKey, agentsApi.tools);
  const [tab, setTab] = useState<EditorTab>("definition");
  const [form, setForm] = useState<AgentForm>(() => (agent ? formFromAgent(agent) : emptyAgentForm()));
  const [showErrors, setShowErrors] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const errors = validateAgentForm(form, { creating });
  const invalid = Object.keys(errors).length > 0;

  async function save() {
    setShowErrors(true);
    if (invalid) {
      setTab("definition");
      // After the errors render: focus the first invalid field, or show the first message.
      requestAnimationFrame(() => {
        const field = formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]');
        if (field) field.focus();
        else formRef.current?.querySelector(".text-destructive")?.scrollIntoView({ block: "center" });
      });
      return;
    }
    setPending(true);
    setError(null);
    try {
      const saved = creating ? await agentsApi.create(toCreatePayload(form)) : await agentsApi.update(form.key, toUpdatePayload(form));
      await invalidateQueries(agentsKey);
      await invalidateQueries(agentVersionsKey(saved.key));
      toast({
        title: creating ? `${saved.name} created` : `${saved.name} saved as version ${saved.version}`,
        description: AGENT_SAVED_TOAST,
      });
      onDone();
    } catch (err) {
      setError(err);
      setTab("definition");
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      ref={formRef}
      className="contents"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <DialogHeader>
        <DialogTitle>{creating ? "New agent" : `Edit ${agent.name}`}</DialogTitle>
        <DialogDescription>
          {creating ? "Changes take effect on the next question; no deploy needed." : `Version ${agent.version}. Saving creates version ${agent.version + 1}; earlier versions can be restored.`}
        </DialogDescription>
      </DialogHeader>

      <Tabs value={tab} onValueChange={(value) => setTab(value as EditorTab)} className="flex flex-col gap-4">
        <TabsList aria-label="Agent editor">
          <TabsTrigger value="definition">Definition</TabsTrigger>
          <TabsTrigger value="test">Test console</TabsTrigger>
          <TabsTrigger value="versions" disabled={creating} title={creating ? "Save the agent to start its history" : undefined}>
            Versions
          </TabsTrigger>
        </TabsList>
        <TabsContent value="definition" keepMounted>
          <DefinitionFields
            form={form}
            setForm={setForm}
            creating={creating}
            errors={errors}
            showErrors={showErrors}
            error={error}
            collections={collections}
            collectionsError={collectionsError}
            tools={tools.state.status === "success" ? tools.state.data : null}
            toolsError={tools.state.status === "error" ? tools.state.error : null}
            catalog={catalog}
            catalogFailed={catalogFailed}
          />
        </TabsContent>
        <TabsContent value="test" keepMounted>
          <AgentTestConsole form={form} savedKey={agent?.key ?? null} formInvalid={invalid} />
        </TabsContent>
        {agent ? (
          <TabsContent value="versions">
            <AgentVersionsPanel agent={agent} onRestored={onDone} />
          </TabsContent>
        ) : null}
      </Tabs>

      <div className="flex flex-col gap-3 border-t pt-3">
        <Field
          label="What changed (optional)"
          hint="Saved with the new version, so others can see why the agent was changed."
          error={showErrors ? (errors.note ?? null) : null}
        >
          {({ id, describedBy, invalid: noteInvalid }) => (
            <Input
              id={id}
              maxLength={AGENT_LIMITS.noteMax}
              value={form.note}
              placeholder={creating ? "e.g. First version of the Legal agent" : "e.g. Added NDA examples"}
              onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))}
              aria-describedby={describedBy}
              aria-invalid={noteInvalid || undefined}
            />
          )}
        </Field>
        {showErrors && invalid && tab !== "definition" ? (
          <p className="text-xs text-destructive" role="alert">
            Some fields in Definition need attention.
          </p>
        ) : null}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
            Cancel
          </Button>
          <Button type="submit" disabled={pending}>
            {pending ? "Saving…" : creating ? "Create agent" : "Save agent"}
          </Button>
        </DialogFooter>
      </div>
    </form>
  );
}

function DefinitionFields({
  form,
  setForm,
  creating,
  errors,
  showErrors,
  error,
  collections,
  collectionsError,
  tools,
  toolsError,
  catalog,
  catalogFailed,
}: {
  form: AgentForm;
  setForm: (update: (form: AgentForm) => AgentForm) => void;
  creating: boolean;
  errors: ReturnType<typeof validateAgentForm>;
  showErrors: boolean;
  error: unknown;
  collections: AdminCollection[];
  collectionsError: unknown;
  tools: AgentTool[] | null;
  toolsError: unknown;
  catalog: ModelCatalog | null;
  catalogFailed: boolean;
}) {
  const server = Object.fromEntries(fieldErrors(error).map((f) => [f.field, f.message]));
  const code = errorMessage(error).code;
  const keyError = code === "slug_taken" ? "An agent with this key already exists." : (server.key ?? null);
  const toolError = code === "tool_not_allowed" || code === "unknown_tool" ? errorMessage(error).description : null;
  const modelError = code === "unknown_model" || code === "model_disabled" ? errorMessage(error).description : null;
  const shown = (field: keyof typeof errors) => (showErrors ? (errors[field] ?? null) : null);
  const patch = (change: Partial<AgentForm>) => setForm((f) => ({ ...f, ...change }));
  const handled = keyError || toolError || modelError || code === "unknown_collection";

  return (
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

      <Field
        label="Description"
        hint="Written for the planner: what can this agent answer, and what is it not for?"
        error={shown("description") ?? server.description ?? null}
      >
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
                placeholder="e.g. Who has to sign a supplier contract worth 80,000 euros?"
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

      {toolsError ? (
        <ErrorAlert error={toolsError} />
      ) : tools === null ? (
        <TableSkeleton rows={2} />
      ) : (
        <AgentToolsPicker
          tools={tools}
          selected={form.tools}
          onChange={(next) => setForm((f) => withTools(f, next))}
          error={toolError ?? shown("tools")}
        />
      )}

      <Field
        label="Model"
        hint={
          catalog
            ? "Its own model, or the model Admin › Models sets for agents. Planning and the final answer keep their own models."
            : catalogFailed
              ? "The model catalog could not be loaded, so only the default is offered."
              : "This deployment has no model catalog: agents use the default model for their role."
        }
        error={modelError}
      >
        {({ id, describedBy }) => (
          <SimpleSelect
            id={id}
            describedBy={describedBy}
            value={form.model}
            onChange={(model) => patch({ model })}
            options={modelOptions(catalog, form.model === DEFAULT_MODEL ? null : form.model)}
          />
        )}
      </Field>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-medium">Limits</legend>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Searches per question" hint="More searches find more, but take longer.">
            {({ id, describedBy }) => (
              <SimpleSelect
                id={id}
                describedBy={describedBy}
                value={String(form.maxSearches)}
                onChange={(v) => patch({ maxSearches: Number(v) })}
                options={[1, 2, 3, 4, 5].map((n) => ({ value: String(n), label: n === 1 ? "1 search" : `Up to ${n} searches` }))}
              />
            )}
          </Field>
          <Field
            label="Tool calls per question"
            hint={form.tools.length ? "Each call is audited." : "Choose a tool first."}
            error={shown("maxToolCalls")}
          >
            {({ id, describedBy, invalid }) => (
              <SimpleSelect
                id={id}
                describedBy={describedBy}
                invalid={invalid}
                disabled={form.tools.length === 0}
                value={String(form.maxToolCalls)}
                onChange={(v) => patch({ maxToolCalls: Number(v) })}
                options={[0, 1, 2, 3, 4, 5].map((n) => ({ value: String(n), label: n === 0 ? "None" : n === 1 ? "1 call" : `Up to ${n} calls` }))}
              />
            )}
          </Field>
          <Field label="Output tokens per call" hint="Caps each model call of the agent, thinking included." error={shown("maxOutputTokens")}>
            {({ id, describedBy, invalid }) => (
              <SimpleSelect
                id={id}
                describedBy={describedBy}
                invalid={invalid}
                value={String(form.maxOutputTokens)}
                onChange={(v) => patch({ maxOutputTokens: Number(v) })}
                options={[...new Set([...OUTPUT_TOKEN_CHOICES, form.maxOutputTokens])]
                  .sort((a, b) => a - b)
                  .map((n) => ({ value: String(n), label: n.toLocaleString("en") }))}
              />
            )}
          </Field>
        </div>
      </fieldset>

      <div className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2">
        <div>
          <p className="text-sm font-medium">Enabled</p>
          <p className="text-xs text-muted-foreground">Disabled agents are never called.</p>
        </div>
        <Switch checked={form.enabled} onCheckedChange={(checked) => patch({ enabled: checked })} aria-label="Enabled" />
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

      {error && !handled ? <ErrorAlert error={error} /> : null}
    </div>
  );
}
