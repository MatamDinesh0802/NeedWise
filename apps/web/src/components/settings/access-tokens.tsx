"use client";

import { Check, Copy, KeyRound, Plus, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { ConfirmDialog } from "@/components/data/confirm-dialog";
import { Field, SimpleSelect } from "@/components/data/form";
import { EmptyState, ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import { useStore } from "@/hooks/use-store";
import { formatDate, formatRelative } from "@/lib/format";
import {
  createTokenFlow,
  DEFAULT_EXPIRY_DAYS,
  EXPIRY_OPTIONS,
  LATER_PHASE_NOTE,
  TOKEN_NAME_MAX,
  TOKEN_SCOPES,
  tokensApi,
  tokensKey,
  tokenStatus,
  type AccessToken,
  type CreateTokenFlow,
} from "@/lib/tokens";

const STATUS_STYLE = {
  active: { label: "Active", className: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" },
  expired: { label: "Expired", className: "bg-muted text-muted-foreground" },
  revoked: { label: "Revoked", className: "bg-muted text-muted-foreground line-through" },
} as const;

export function AccessTokensCard() {
  const { state, refetch } = useApiQuery(tokensKey, tokensApi.list);
  const [creating, setCreating] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<AccessToken | null>(null);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Access tokens
          <RefreshingHint active={state.status !== "loading" && state.refreshing} />
        </CardTitle>
        <CardDescription>
          Personal access tokens let tools such as scripts, the CLI or MCP clients call NeedWise as you, limited to the
          scopes you choose. Treat them like passwords.
        </CardDescription>
        <CardAction>
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus data-icon="inline-start" />
            New token
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        {state.status === "loading" ? (
          <TableSkeleton rows={2} />
        ) : state.status === "error" ? (
          <ErrorAlert error={state.error} onRetry={() => void refetch()} />
        ) : state.data.items.length === 0 ? (
          <EmptyState icon={KeyRound} title="No access tokens" description="Create one when a tool needs to call NeedWise on your behalf." />
        ) : (
          <TokenTable tokens={state.data.items} onRevoke={setRevokeTarget} />
        )}
      </CardContent>

      <CreateTokenDialog open={creating} onOpenChange={setCreating} />

      <ConfirmDialog
        open={revokeTarget !== null}
        onOpenChange={(open) => !open && setRevokeTarget(null)}
        title={`Revoke “${revokeTarget?.name ?? ""}”?`}
        description="Anything using this token stops working immediately. This can't be undone; create a new token if you need access again."
        confirmLabel="Revoke token"
        onConfirm={async () => {
          if (!revokeTarget) return;
          await tokensApi.revoke(revokeTarget.id);
          await invalidateQueries(tokensKey);
        }}
      />
    </Card>
  );
}

function TokenTable({ tokens, onRevoke }: { tokens: AccessToken[]; onRevoke: (token: AccessToken) => void }) {
  const now = new Date();
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Token</TableHead>
          <TableHead>Scopes</TableHead>
          <TableHead>Created</TableHead>
          <TableHead>Expires</TableHead>
          <TableHead>Last used</TableHead>
          <TableHead>Status</TableHead>
          <TableHead className="w-20">
            <span className="sr-only">Revoke</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {tokens.map((token) => {
          const status = tokenStatus(token, now);
          const style = STATUS_STYLE[status];
          return (
            <TableRow key={token.id}>
              <TableCell className="font-medium">{token.name}</TableCell>
              <TableCell className="font-mono text-xs text-muted-foreground">{token.prefix}…</TableCell>
              <TableCell>
                <span className="flex flex-wrap gap-1">
                  {token.scopes.map((scope) => (
                    <Badge key={scope} variant="outline" className="font-mono">
                      {scope}
                    </Badge>
                  ))}
                </span>
              </TableCell>
              <TableCell className="whitespace-nowrap">{formatDate(token.created_at)}</TableCell>
              <TableCell className="whitespace-nowrap" title={formatDate(token.expires_at)}>
                {formatRelative(token.expires_at, now)}
              </TableCell>
              <TableCell className="whitespace-nowrap text-muted-foreground">{formatRelative(token.last_used_at, now)}</TableCell>
              <TableCell>
                <Badge variant="secondary" className={style.className}>
                  {style.label}
                </Badge>
              </TableCell>
              <TableCell>
                {status === "active" ? (
                  <Button variant="ghost" size="sm" className="text-destructive" onClick={() => onRevoke(token)}>
                    Revoke
                  </Button>
                ) : null}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

function CreateTokenDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  // One flow per dialog; "Done" (or closing) wipes the plain token from memory.
  const [flow] = useState<CreateTokenFlow>(() =>
    createTokenFlow({ onCreated: () => void invalidateQueries(tokensKey) }),
  );
  const state = useStore(flow);

  function change(next: boolean) {
    if (state.step === "creating") return;
    if (!next) flow.finish();
    onOpenChange(next);
  }

  return (
    <Dialog open={open} onOpenChange={change}>
      <DialogContent className="sm:max-w-lg">
        {state.step === "reveal" ? (
          <RevealToken
            token={state.token}
            name={state.record.name}
            copy={state.copy}
            onCopy={() => void flow.copy()}
            onDone={() => change(false)}
          />
        ) : open ? (
          <TokenForm
            flow={flow}
            pending={state.step === "creating"}
            error={state.step === "form" ? state.error : null}
            fieldErrors={state.step === "form" ? state.fieldErrors : {}}
            onCancel={() => change(false)}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function TokenForm({
  flow,
  pending,
  error,
  fieldErrors,
  onCancel,
}: {
  flow: CreateTokenFlow;
  pending: boolean;
  error: { title: string; description: string; code: string | null } | null;
  fieldErrors: Partial<Record<"name" | "scopes" | "expiresInDays", string>>;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["profile:read"]);
  const [expiry, setExpiry] = useState(String(DEFAULT_EXPIRY_DAYS));

  return (
    <form
      className="contents"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        void flow.submit({ name, scopes, expiresInDays: Number(expiry) });
      }}
    >
      <DialogHeader>
        <DialogTitle>New access token</DialogTitle>
        <DialogDescription>The token is shown once, right after you create it.</DialogDescription>
      </DialogHeader>
      <div className="flex flex-col gap-4">
        <Field label="Name" hint="Something that tells you where it's used, e.g. “Laptop CLI”." error={fieldErrors.name}>
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              maxLength={TOKEN_NAME_MAX}
              value={name}
              onChange={(e) => setName(e.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid || undefined}
            />
          )}
        </Field>

        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-sm font-medium">Scopes</legend>
          {TOKEN_SCOPES.map((scope) => {
            const id = `scope-${scope.value.replace(":", "-")}`;
            return (
              <div key={scope.value} className="flex items-start gap-2.5">
                <Checkbox
                  id={id}
                  checked={scopes.includes(scope.value)}
                  onCheckedChange={(checked) =>
                    setScopes((current) =>
                      checked ? [...current, scope.value] : current.filter((s) => s !== scope.value),
                    )
                  }
                  className="mt-0.5"
                />
                <div className="grid gap-0.5">
                  <Label htmlFor={id} className="flex flex-wrap gap-2">
                    {scope.label}
                    <span className="font-mono text-xs font-normal text-muted-foreground">{scope.value}</span>
                  </Label>
                  <p className="text-xs text-muted-foreground">
                    {scope.description}
                    {scope.active ? null : <span className="font-medium"> {LATER_PHASE_NOTE}</span>}
                  </p>
                </div>
              </div>
            );
          })}
          {fieldErrors.scopes ? <p className="text-xs text-destructive">{fieldErrors.scopes}</p> : null}
        </fieldset>

        <Field label="Expires after" error={fieldErrors.expiresInDays}>
          {({ id }) => (
            <SimpleSelect
              id={id}
              value={expiry}
              onChange={setExpiry}
              options={EXPIRY_OPTIONS.map((days) => ({ value: String(days), label: days === 365 ? "1 year" : `${days} days` }))}
            />
          )}
        </Field>
      </div>

      {error ? (
        <Alert variant="destructive">
          <TriangleAlert aria-hidden="true" />
          <AlertTitle>{error.title}</AlertTitle>
          <AlertDescription>{error.description}</AlertDescription>
        </Alert>
      ) : null}

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? "Creating…" : "Create token"}
        </Button>
      </DialogFooter>
    </form>
  );
}

function RevealToken({
  token,
  name,
  copy,
  onCopy,
  onDone,
}: {
  token: string;
  name: string;
  copy: "idle" | "copied" | "failed";
  onCopy: () => void;
  onDone: () => void;
}) {
  return (
    <>
      <DialogHeader>
        <DialogTitle>Copy your new token</DialogTitle>
        <DialogDescription>“{name}” is ready.</DialogDescription>
      </DialogHeader>
      <Alert className="border-amber-500/40 text-amber-800 dark:text-amber-300">
        <TriangleAlert aria-hidden="true" />
        <AlertTitle>You won&apos;t see this token again</AlertTitle>
        <AlertDescription>
          Copy it now and store it somewhere safe, like a password manager. If you lose it, revoke it and create a new one.
        </AlertDescription>
      </Alert>
      <div className="flex gap-2">
        <Input
          readOnly
          value={token}
          aria-label="Your new access token"
          className="font-mono text-xs"
          onFocus={(e) => e.currentTarget.select()}
        />
        <Button type="button" variant="outline" onClick={onCopy} aria-live="polite">
          {copy === "copied" ? <Check data-icon="inline-start" /> : <Copy data-icon="inline-start" />}
          {copy === "copied" ? "Copied" : "Copy"}
        </Button>
      </div>
      {copy === "failed" ? (
        <p className="text-xs text-destructive" role="alert">
          Couldn&apos;t copy automatically. Select the token and copy it manually.
        </p>
      ) : null}
      <DialogFooter>
        <Button type="button" onClick={onDone}>
          Done
        </Button>
      </DialogFooter>
    </>
  );
}
