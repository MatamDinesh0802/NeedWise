"use client";

import { CircleAlert, CircleCheck, Cpu, Info, KeyRound, LoaderCircle, Plus, Server, ShieldCheck, Trash2 } from "lucide-react";
import { useState } from "react";

import { ConfirmDialog } from "@/components/data/confirm-dialog";
import { Field, SimpleSelect } from "@/components/data/form";
import { EmptyState, ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import {
  deleteProviderText,
  emptyModelForm,
  emptyProviderForm,
  formatPrice,
  formFromModel,
  formFromProvider,
  KIND_HINTS,
  KIND_LABELS,
  localModelOptions,
  modelLabel,
  modelsApi,
  modelsKey,
  policyExplanation,
  probeSummary,
  providerName,
  ROLES,
  roleOptions,
  rolesPayload,
  SENSITIVITY_OPTIONS,
  toModelCreate,
  toModelPatch,
  toProviderCreate,
  toProviderPatch,
  validateModelForm,
  validateProviderForm,
  type CatalogModel,
  type DataPolicy,
  type ModelCatalog,
  type ModelForm,
  type ModelProvider,
  type ModelRole,
  type ProbeResult,
  type ProviderForm,
  type ProviderKind,
  type RoleModels,
  type Sensitivity,
} from "@/lib/admin/models";
import { errorMessage } from "@/lib/error-messages";
import type { QueryState } from "@/lib/stores/query-cache";
import { toast } from "@/lib/stores/toasts";
import { cn } from "@/lib/utils";

const NO_LOCAL = "none";
// Full width, and long model names are cut rather than pushing the page sideways on phones.
const WIDE_SELECT = "w-full min-w-0 *:data-[slot=select-value]:min-w-0 *:data-[slot=select-value]:overflow-hidden";

function notify(error: unknown) {
  const message = errorMessage(error);
  toast({ title: message.title, description: message.description, tone: "error" });
}

export function ModelsSection() {
  const query = useApiQuery(modelsKey, modelsApi.get);
  return <ModelsView state={query.state} onRetry={() => void query.refetch()} />;
}

/** Loading, error and loaded states of the whole page. */
export function ModelsView({ state, onRetry }: { state: QueryState<ModelCatalog>; onRetry?: () => void }) {
  return (
    <section className="flex flex-col gap-8" aria-labelledby="models-heading">
      <div>
        <h3 id="models-heading" className="flex items-center gap-2 text-lg font-semibold">
          Models
          <RefreshingHint active={state.status === "success" && state.refreshing} />
        </h3>
        <p className="text-sm text-muted-foreground">
          Bring your own API keys, keep a catalog of models with their prices, choose which model does what, and decide what
          may leave your infrastructure.
        </p>
      </div>
      {state.status === "loading" ? (
        <TableSkeleton rows={6} />
      ) : state.status === "error" ? (
        <ErrorAlert error={state.error} onRetry={onRetry} />
      ) : (
        <ModelsPanels catalog={state.data} />
      )}
    </section>
  );
}

export function ModelsPanels({ catalog }: { catalog: ModelCatalog }) {
  // Forms restart from the server's values after every save (the key changes with them).
  const rolesKey = JSON.stringify(catalog.roles);
  const policyKey = JSON.stringify(catalog.policy);
  return (
    <>
      <ProvidersPanel catalog={catalog} />
      <CatalogPanel catalog={catalog} />
      <RolesPanel key={rolesKey} catalog={catalog} />
      <PolicyPanel key={policyKey} catalog={catalog} />
    </>
  );
}

// --- Providers -------------------------------------------------------------------------------

export function ProvidersPanel({ catalog }: { catalog: ModelCatalog }) {
  const [editing, setEditing] = useState<ModelProvider | "new" | null>(null);
  const [deleting, setDeleting] = useState<ModelProvider | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [toggling, setToggling] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, ProbeResult>>({});
  const own = catalog.providers.filter((p) => !p.system);
  const countModels = (id: string) => catalog.models.filter((m) => m.provider_id === id).length;
  const confirmText = deleting ? deleteProviderText(deleting, countModels(deleting.id)) : null;

  async function test(provider: ModelProvider) {
    setTesting(provider.id);
    try {
      const result = await modelsApi.testProvider(provider.id);
      setResults((r) => ({ ...r, [provider.id]: result }));
    } catch (error) {
      notify(error);
    } finally {
      setTesting(null);
    }
  }

  async function toggle(provider: ModelProvider, enabled: boolean) {
    setToggling(provider.id);
    try {
      await modelsApi.updateProvider(provider.id, { enabled });
      await invalidateQueries(modelsKey);
    } catch (error) {
      notify(error);
    } finally {
      setToggling(null);
    }
  }

  return (
    <section className="flex flex-col gap-3" aria-labelledby="providers-heading">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h4 id="providers-heading" className="font-semibold">
            Providers
          </h4>
          <p className="text-sm text-muted-foreground">
            Where requests go. Keys are encrypted at rest and never shown again: only their last four characters.
          </p>
        </div>
        <Button onClick={() => setEditing("new")}>
          <Plus data-icon="inline-start" />
          Add provider
        </Button>
      </div>
      <ul className="grid gap-3 md:grid-cols-2">
        {catalog.providers.map((provider) => (
          <ProviderCard
            key={provider.id}
            provider={provider}
            models={countModels(provider.id)}
            result={results[provider.id] ?? null}
            testing={testing === provider.id}
            toggling={toggling === provider.id}
            onTest={() => void test(provider)}
            onEdit={() => setEditing(provider)}
            onDelete={() => setDeleting(provider)}
            onToggle={(enabled) => void toggle(provider, enabled)}
          />
        ))}
      </ul>
      {own.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Only the deployment&apos;s provider so far. Add one to use your organization&apos;s own key or a model server you run.
        </p>
      ) : null}

      <Dialog open={editing !== null} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
          {editing !== null ? (
            <ProviderFormView
              key={editing === "new" ? "new" : editing.id}
              provider={editing === "new" ? null : editing}
              onDone={() => setEditing(null)}
            />
          ) : null}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={confirmText?.title ?? "Delete provider?"}
        description={confirmText?.description ?? ""}
        confirmLabel={confirmText?.confirm ?? "Delete provider"}
        onConfirm={async () => {
          if (!deleting) return;
          await modelsApi.deleteProvider(deleting.id);
          await invalidateQueries(modelsKey);
          toast({ title: `${deleting.name} deleted`, description: "Its key and models are gone." });
        }}
      />
    </section>
  );
}

export function ProviderCard({
  provider,
  models,
  result,
  testing = false,
  toggling = false,
  onTest,
  onEdit,
  onDelete,
  onToggle,
}: {
  provider: ModelProvider;
  models: number;
  result: ProbeResult | null;
  testing?: boolean;
  toggling?: boolean;
  onTest?: () => void;
  onEdit?: () => void;
  onDelete?: () => void;
  onToggle?: (enabled: boolean) => void;
}) {
  const resultId = `probe-${provider.id}`;
  return (
    <li className={cn("flex flex-col gap-2 rounded-xl border bg-card p-4", !provider.enabled && "opacity-70")}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 font-medium">
            {provider.name}
            {provider.system ? <Badge variant="secondary">Deployment · read-only</Badge> : null}
          </p>
          <p className="text-xs text-muted-foreground">{KIND_LABELS[provider.kind]}</p>
        </div>
        {provider.system ? null : (
          <label className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
            {provider.enabled ? "Enabled" : "Disabled"}
            <Switch
              checked={provider.enabled}
              disabled={toggling}
              onCheckedChange={(checked) => onToggle?.(checked)}
              aria-label={`${provider.enabled ? "Disable" : "Enable"} ${provider.name}`}
            />
          </label>
        )}
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-muted-foreground">URL</dt>
        <dd className="truncate font-mono text-xs leading-5" title={provider.base_url ?? undefined}>
          {provider.base_url ?? (provider.kind === "anthropic" ? "api.anthropic.com" : "not set")}
        </dd>
        <dt className="text-muted-foreground">Data</dt>
        <dd className="flex items-center gap-1.5">
          {provider.local ? (
            <>
              <Server className="size-3.5" aria-hidden="true" /> Local: stays on your infrastructure
            </>
          ) : (
            <>
              <Cpu className="size-3.5" aria-hidden="true" /> External: leaves your infrastructure
            </>
          )}
        </dd>
        <dt className="text-muted-foreground">Key</dt>
        <dd className="flex items-center gap-1.5">
          <KeyRound className="size-3.5" aria-hidden="true" />
          {provider.system
            ? provider.has_key
              ? "Set by the server's configuration"
              : "None configured"
            : provider.has_key
              ? `Stored, ends in ${provider.key_hint?.replace("…", "") || "(hidden)"}`
              : "No key"}
        </dd>
      </dl>
      <p className="text-xs text-muted-foreground">
        {models} {models === 1 ? "model" : "models"} in the catalog
        {provider.system ? " · comes from the server's settings (LLM_* and LOCAL_LLM_*)" : ""}
      </p>
      <div aria-live="polite" id={resultId}>
        {result ? (
          <p className={cn("flex items-start gap-1.5 text-sm", result.ok ? "text-emerald-700 dark:text-emerald-400" : "text-destructive")}>
            {result.ok ? <CircleCheck className="mt-0.5 size-4 shrink-0" aria-hidden="true" /> : <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />}
            {probeSummary(result)}
          </p>
        ) : null}
      </div>
      <div className="mt-auto flex flex-wrap justify-end gap-1 pt-1">
        <Button variant="outline" size="sm" onClick={onTest} disabled={testing} aria-label={`Test ${provider.name}`} aria-describedby={resultId}>
          {testing ? <LoaderCircle className="animate-spin" data-icon="inline-start" aria-hidden="true" /> : null}
          {testing ? "Testing…" : "Test"}
        </Button>
        {provider.system ? null : (
          <>
            <Button variant="ghost" size="sm" onClick={onDelete} aria-label={`Delete ${provider.name}`}>
              <Trash2 data-icon="inline-start" aria-hidden="true" />
              Delete
            </Button>
            <Button variant="outline" size="sm" onClick={onEdit} aria-label={`Edit ${provider.name}`}>
              Edit
            </Button>
          </>
        )}
      </div>
    </li>
  );
}

export function ProviderFormView({ provider, onDone }: { provider: ModelProvider | null; onDone: () => void }) {
  const creating = provider === null;
  const [form, setForm] = useState<ProviderForm>(() => (provider ? formFromProvider(provider) : emptyProviderForm()));
  const [showErrors, setShowErrors] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const errors = validateProviderForm(form, provider);
  const shown = (field: keyof typeof errors) => (showErrors ? (errors[field] ?? null) : null);
  const patch = (change: Partial<ProviderForm>) => setForm((f) => ({ ...f, ...change }));

  async function save() {
    setShowErrors(true);
    if (Object.keys(errors).length > 0) return;
    setPending(true);
    setError(null);
    try {
      const saved = provider
        ? await modelsApi.updateProvider(provider.id, toProviderPatch(form, provider))
        : await modelsApi.createProvider(toProviderCreate(form));
      setForm((f) => ({ ...f, apiKey: "" })); // never kept in the page longer than needed
      await invalidateQueries(modelsKey);
      toast({ title: creating ? `${saved.name} added` : `${saved.name} saved`, description: "Used from the next request on." });
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
      autoComplete="off"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <DialogHeader>
        <DialogTitle>{creating ? "Add a provider" : `Edit ${provider.name}`}</DialogTitle>
        <DialogDescription>{KIND_HINTS[form.kind]}</DialogDescription>
      </DialogHeader>
      <div className="flex flex-col gap-4">
        <Field label="Name" error={shown("name")}>
          {({ id, describedBy, invalid }) => (
            <Input id={id} maxLength={100} value={form.name} onChange={(e) => patch({ name: e.target.value })} aria-describedby={describedBy} aria-invalid={invalid || undefined} />
          )}
        </Field>
        <Field label="Kind" hint={creating ? undefined : "Can't be changed: add another provider instead."}>
          {({ id, describedBy }) => (
            <SimpleSelect<ProviderKind>
              id={id}
              describedBy={describedBy}
              value={form.kind}
              disabled={!creating}
              onChange={(kind) => patch({ kind, local: kind === "anthropic" ? false : form.local })}
              options={[
                { value: "openai_compatible", label: KIND_LABELS.openai_compatible },
                { value: "anthropic", label: KIND_LABELS.anthropic },
              ]}
            />
          )}
        </Field>
        <Field
          label={form.kind === "anthropic" ? "Base URL (optional)" : "Base URL"}
          hint="Your key is only ever sent to this URL. Changing it needs the key again."
          error={shown("baseUrl")}
        >
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              className="font-mono"
              inputMode="url"
              spellCheck={false}
              placeholder={form.kind === "anthropic" ? "https://api.anthropic.com" : "https://api.openai.com/v1"}
              value={form.baseUrl}
              onChange={(e) => patch({ baseUrl: e.target.value })}
              aria-describedby={describedBy}
              aria-invalid={invalid || undefined}
            />
          )}
        </Field>
        <Field
          label={provider?.has_key ? "New API key (leave empty to keep the stored one)" : "API key (optional for local servers)"}
          hint={provider?.has_key ? `A key is stored, ending in ${provider.key_hint?.replace("…", "") ?? "…"}. It is never shown.` : "Stored encrypted. Never shown again, not even to you."}
          error={shown("apiKey")}
        >
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              type="password"
              autoComplete="new-password"
              spellCheck={false}
              value={form.apiKey}
              onChange={(e) => patch({ apiKey: e.target.value, clearKey: false })}
              aria-describedby={describedBy}
              aria-invalid={invalid || undefined}
            />
          )}
        </Field>
        {provider?.has_key ? (
          <label className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-sm">
            <span>
              <span className="font-medium">Remove the stored key</span>
              <span className="block text-xs text-muted-foreground">Requests go without a key until you add one.</span>
            </span>
            <Switch checked={form.clearKey} disabled={Boolean(form.apiKey.trim())} onCheckedChange={(checked) => patch({ clearKey: checked })} aria-label="Remove the stored key" />
          </label>
        ) : null}
        <div className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2">
          <div>
            <p className="text-sm font-medium">Runs on our infrastructure (local)</p>
            <p className="text-xs text-muted-foreground">
              Only for servers you control. The data policy may send sensitive sources to local models.
            </p>
            {shown("local") ? <p className="text-xs text-destructive">{errors.local}</p> : null}
          </div>
          <Switch
            checked={form.local}
            disabled={form.kind === "anthropic"}
            onCheckedChange={(checked) => patch({ local: checked })}
            aria-label="Runs on our infrastructure (local)"
          />
        </div>
        <div className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2">
          <p className="text-sm font-medium">Enabled</p>
          <Switch checked={form.enabled} onCheckedChange={(checked) => patch({ enabled: checked })} aria-label="Enabled" />
        </div>
      </div>
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? "Saving…" : creating ? "Add provider" : "Save provider"}
        </Button>
      </DialogFooter>
    </form>
  );
}

// --- Catalog ---------------------------------------------------------------------------------

export function CatalogPanel({ catalog }: { catalog: ModelCatalog }) {
  const [editing, setEditing] = useState<CatalogModel | "new" | null>(null);
  const [deleting, setDeleting] = useState<CatalogModel | null>(null);
  const own = catalog.providers.filter((p) => !p.system);
  const tenantModels = catalog.models.filter((m) => own.some((p) => p.id === m.provider_id));

  return (
    <section className="flex flex-col gap-3" aria-labelledby="catalog-heading">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h4 id="catalog-heading" className="font-semibold">
            Model catalog
          </h4>
          <p className="text-sm text-muted-foreground">
            Prices are in US dollars per million tokens and drive the cost shown with each answer. A model without a price
            shows its cost as unknown.
          </p>
        </div>
        <Button onClick={() => setEditing("new")} disabled={own.length === 0} title={own.length === 0 ? "Add a provider first" : undefined}>
          <Plus data-icon="inline-start" />
          Add model
        </Button>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Model</TableHead>
            <TableHead>Provider</TableHead>
            <TableHead>Data</TableHead>
            <TableHead>Price</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">
              <span className="sr-only">Actions</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {catalog.models.map((model) => {
            const system = !own.some((p) => p.id === model.provider_id);
            return (
              <TableRow key={model.id} className={cn(!model.enabled && "opacity-70")}>
                <TableCell>
                  <p className="font-medium">{model.label}</p>
                  <p className="font-mono text-xs text-muted-foreground">{model.name}</p>
                </TableCell>
                <TableCell>{providerName(catalog, model.provider_id)}</TableCell>
                <TableCell>{model.local ? "Local" : "External"}</TableCell>
                <TableCell className="text-sm">{formatPrice(model)}</TableCell>
                <TableCell>{system ? <Badge variant="secondary">Deployment</Badge> : model.enabled ? "Enabled" : "Disabled"}</TableCell>
                <TableCell className="text-right">
                  {system ? null : (
                    <div className="flex justify-end gap-1">
                      <Button variant="ghost" size="sm" onClick={() => setDeleting(model)} aria-label={`Delete ${model.label}`}>
                        <Trash2 data-icon="inline-start" aria-hidden="true" />
                        Delete
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => setEditing(model)} aria-label={`Edit ${model.label}`}>
                        Edit
                      </Button>
                    </div>
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      {tenantModels.length === 0 ? (
        <EmptyState
          icon={Cpu}
          title="No models of your own yet"
          description={own.length === 0 ? "Add a provider, then the models you want to use from it." : "Add the models you want to use from your providers, with their prices."}
        />
      ) : null}

      <Dialog open={editing !== null} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent className="sm:max-w-lg">
          {editing !== null ? (
            <ModelFormView key={editing === "new" ? "new" : editing.id} catalog={catalog} model={editing === "new" ? null : editing} onDone={() => setEditing(null)} />
          ) : null}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={deleting ? `Delete ${deleting.label}?` : "Delete model?"}
        description="It leaves the catalog. If a role, the data policy or an agent still uses it, nothing is deleted and you are told where it is used."
        confirmLabel="Delete model"
        onConfirm={async () => {
          if (!deleting) return;
          await modelsApi.deleteModel(deleting.id);
          await invalidateQueries(modelsKey);
          toast({ title: `${deleting.label} deleted` });
        }}
      />
    </section>
  );
}

export function ModelFormView({ catalog, model, onDone }: { catalog: ModelCatalog; model: CatalogModel | null; onDone: () => void }) {
  const creating = model === null;
  const own = catalog.providers.filter((p) => !p.system);
  const [form, setForm] = useState<ModelForm>(() => (model ? formFromModel(model) : emptyModelForm(own[0]?.id ?? "")));
  const [showErrors, setShowErrors] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const errors = validateModelForm(form);
  const shown = (field: keyof ModelForm) => (showErrors ? (errors[field] ?? null) : null);
  const patch = (change: Partial<ModelForm>) => setForm((f) => ({ ...f, ...change }));

  async function save() {
    setShowErrors(true);
    if (Object.keys(errors).length > 0) return;
    setPending(true);
    setError(null);
    try {
      const saved = model ? await modelsApi.updateModel(model.id, toModelPatch(form, model)) : await modelsApi.createModel(toModelCreate(form));
      await invalidateQueries(modelsKey);
      toast({ title: creating ? `${saved.label} added` : `${saved.label} saved` });
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
        <DialogTitle>{creating ? "Add a model" : `Edit ${model.label}`}</DialogTitle>
        <DialogDescription>{creating ? "The name is what the provider calls the model; it can't be changed later." : "The model's name can't be changed: add it again under its new name."}</DialogDescription>
      </DialogHeader>
      <div className="flex flex-col gap-4">
        <Field label="Provider" error={shown("providerId")}>
          {({ id, describedBy }) => (
            <SimpleSelect
              id={id}
              describedBy={describedBy}
              value={form.providerId}
              disabled={!creating}
              onChange={(providerId) => patch({ providerId })}
              options={own.map((p) => ({ value: p.id, label: p.name }))}
            />
          )}
        </Field>
        <Field label="Model name" hint="Exactly as the provider expects it, e.g. gpt-5.1 or qwen3:14b." error={shown("name")}>
          {({ id, describedBy, invalid }) => (
            <Input id={id} className="font-mono" spellCheck={false} readOnly={!creating} disabled={!creating} value={form.name} onChange={(e) => patch({ name: e.target.value })} aria-describedby={describedBy} aria-invalid={invalid || undefined} />
          )}
        </Field>
        <Field label="Label (optional)" hint="What admins see in the lists. Defaults to the name." error={shown("label")}>
          {({ id, describedBy, invalid }) => (
            <Input id={id} maxLength={200} value={form.label} onChange={(e) => patch({ label: e.target.value })} aria-describedby={describedBy} aria-invalid={invalid || undefined} />
          )}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Input price (USD per 1M tokens)" error={shown("inputPrice")}>
            {({ id, describedBy, invalid }) => (
              <Input id={id} inputMode="decimal" placeholder="e.g. 2.00" value={form.inputPrice} onChange={(e) => patch({ inputPrice: e.target.value })} aria-describedby={describedBy} aria-invalid={invalid || undefined} />
            )}
          </Field>
          <Field label="Output price (USD per 1M tokens)" error={shown("outputPrice")}>
            {({ id, describedBy, invalid }) => (
              <Input id={id} inputMode="decimal" placeholder="e.g. 10.00" value={form.outputPrice} onChange={(e) => patch({ outputPrice: e.target.value })} aria-describedby={describedBy} aria-invalid={invalid || undefined} />
            )}
          </Field>
        </div>
        <div className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2">
          <p className="text-sm font-medium">Enabled</p>
          <Switch checked={form.enabled} onCheckedChange={(checked) => patch({ enabled: checked })} aria-label="Enabled" />
        </div>
      </div>
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? "Saving…" : creating ? "Add model" : "Save model"}
        </Button>
      </DialogFooter>
    </form>
  );
}

// --- Roles -----------------------------------------------------------------------------------

export function RolesPanel({ catalog }: { catalog: ModelCatalog }) {
  const [chosen, setChosen] = useState<RoleModels>(catalog.roles);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const changed = ROLES.some(({ role }) => chosen[role] !== catalog.roles[role]);
  const onDefaults = ROLES.every(({ role }) => catalog.roles[role] === catalog.defaults.roles[role]);

  async function save(roles: Record<ModelRole, string | null>) {
    setPending(true);
    setError(null);
    try {
      await modelsApi.putRoles(roles);
      await invalidateQueries(modelsKey);
      toast({ title: "Roles saved", description: "Used from the next request on." });
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="flex flex-col gap-3" aria-labelledby="roles-heading">
      <div>
        <h4 id="roles-heading" className="font-semibold">
          Which model does what
        </h4>
        <p className="text-sm text-muted-foreground">
          Each kind of work goes to one model. An agent can also name its own model; otherwise it uses the Agents role.
        </p>
      </div>
      <form
        className="flex flex-col gap-4 rounded-xl border p-4"
        onSubmit={(e) => {
          e.preventDefault();
          void save(rolesPayload(chosen, catalog.defaults.roles));
        }}
      >
        <div className="grid gap-4 md:grid-cols-2">
          {ROLES.map(({ role, label, description }) => (
            <Field key={role} label={label} hint={description} className="min-w-0">
              {({ id, describedBy }) => (
                <SimpleSelect
                  id={id}
                  describedBy={describedBy}
                  value={chosen[role]}
                  onChange={(value) => setChosen((c) => ({ ...c, [role]: value }))}
                  options={roleOptions(catalog, role)}
                  className={WIDE_SELECT}
                />
              )}
            </Field>
          ))}
        </div>
        {error ? <ErrorAlert error={error} /> : null}
        <div className="flex flex-wrap justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={pending || onDefaults}
            onClick={() => void save(Object.fromEntries(ROLES.map(({ role }) => [role, null])) as Record<ModelRole, null>)}
          >
            Use the deployment&apos;s defaults
          </Button>
          <Button type="submit" disabled={pending || !changed}>
            {pending ? "Saving…" : "Save roles"}
          </Button>
        </div>
      </form>
    </section>
  );
}

// --- Data policy -----------------------------------------------------------------------------

export function PolicyPanel({ catalog }: { catalog: ModelCatalog }) {
  const [policy, setPolicy] = useState<DataPolicy>(catalog.policy);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const locals = localModelOptions(catalog);
  const changed = policy.max_external_sensitivity !== catalog.policy.max_external_sensitivity || policy.local_model !== catalog.policy.local_model;

  async function save() {
    setPending(true);
    setError(null);
    try {
      await modelsApi.putPolicy(policy);
      await invalidateQueries(modelsKey);
      toast({ title: "Data policy saved", description: "Applies from the next question on." });
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="flex flex-col gap-3" aria-labelledby="policy-heading">
      <div>
        <h4 id="policy-heading" className="flex items-center gap-2 font-semibold">
          <ShieldCheck className="size-4" aria-hidden="true" />
          Data policy for external models
        </h4>
        <p className="text-sm text-muted-foreground">
          Documents carry a sensitivity label. Choose the highest label that may be sent to a model outside your
          infrastructure, and the local model that handles the rest.
        </p>
      </div>
      <form
        className="flex flex-col gap-4 rounded-xl border p-4"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="External models may receive" className="min-w-0">
            {({ id, describedBy }) => (
              <SimpleSelect<Sensitivity>
                id={id}
                describedBy={describedBy}
                value={policy.max_external_sensitivity}
                onChange={(value) => setPolicy((p) => ({ ...p, max_external_sensitivity: value }))}
                options={SENSITIVITY_OPTIONS}
                className={WIDE_SELECT}
              />
            )}
          </Field>
          <Field label="Local model for the rest" className="min-w-0" hint={locals.length === 0 ? "No local models yet: mark a provider as local and add a model to it." : undefined}>
            {({ id, describedBy }) => (
              <SimpleSelect
                id={id}
                describedBy={describedBy}
                value={policy.local_model ?? NO_LOCAL}
                onChange={(value) => setPolicy((p) => ({ ...p, local_model: value === NO_LOCAL ? null : value }))}
                options={[{ value: NO_LOCAL, label: "None: leave those sources out" }, ...locals]}
                className={WIDE_SELECT}
              />
            )}
          </Field>
        </div>
        <Alert>
          <Info aria-hidden="true" />
          <AlertTitle>Where your data goes</AlertTitle>
          <AlertDescription>
            <ul className="flex list-disc flex-col gap-1 pl-4">
              {policyExplanation(catalog, policy).map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
        {error ? <ErrorAlert error={error} /> : null}
        <div className="flex justify-end">
          <Button type="submit" disabled={pending || !changed}>
            {pending ? "Saving…" : "Save data policy"}
          </Button>
        </div>
      </form>
      <p className="text-xs text-muted-foreground">
        Not covered yet: tool results in research mode and query results of the analytics agent are sent to the model the
        role uses, whatever their labels.
      </p>
      <p className="sr-only">Current local model: {modelLabel(catalog, catalog.policy.local_model)}</p>
    </section>
  );
}
