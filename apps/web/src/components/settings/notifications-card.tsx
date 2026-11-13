"use client";

import { BellRing, UserRoundCheck, X } from "lucide-react";
import { useState } from "react";

import { Field, SimpleSelect } from "@/components/data/form";
import { ErrorAlert, RefreshingHint, TableSkeleton } from "@/components/data/states";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ColleaguePicker } from "@/components/work/colleague-picker";
import { useApiQuery } from "@/hooks/use-api-query";
import { usePeople } from "@/hooks/use-people";
import { errorMessage } from "@/lib/error-messages";
import { canUseDirectory } from "@/lib/people";
import type { Me } from "@/lib/session";
import { getQueryCache } from "@/lib/stores/query-cache";
import { toast } from "@/lib/stores/toasts";
import {
  channelSummary,
  DIGEST_HOURS,
  draftFrom,
  EMAIL_MODE_LABELS,
  EMAIL_MODES,
  hourLabel,
  isDirty,
  preferencesApi,
  preferencesKey,
  setChannel,
  toSave,
  usesDigest,
  type EmailMode,
  type PreferencesDraft,
  type WorkPreferences,
} from "@/lib/work/preferences";

/**
 * Settings › Notifications (#notifications: notification emails link here). In-app notifications
 * always happen; this chooses copies by email and Slack, and the backup approver.
 */
export function NotificationsCard({ me }: { me: Me }) {
  const { state, refetch } = useApiQuery(preferencesKey, preferencesApi.get);
  return (
    <Card id="notifications" className="scroll-mt-20">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Notifications and backup approver
          <RefreshingHint active={state.status === "success" && state.refreshing} />
        </CardTitle>
        <CardDescription>
          You always see notifications in the app. Choose which also reach you by email or in Slack, and who decides your
          approvals when you are away.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {state.status === "loading" ? (
          <TableSkeleton rows={4} />
        ) : state.status === "error" ? (
          <ErrorAlert error={state.error} onRetry={() => void refetch()} />
        ) : (
          <PreferencesForm key={JSON.stringify(state.data)} saved={state.data} me={me} />
        )}
      </CardContent>
    </Card>
  );
}

function PreferencesForm({ saved, me }: { saved: WorkPreferences; me: Me }) {
  const [draft, setDraft] = useState<PreferencesDraft>(() => draftFrom(saved));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const dirty = isDirty(draft, saved);

  async function save() {
    setPending(true);
    setError(null);
    try {
      const stored = await preferencesApi.save(toSave(draft));
      getQueryCache().setData<WorkPreferences>(preferencesKey, () => stored);
      toast({ title: "Notification settings saved", description: channelSummary(draftFrom(stored)) });
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      className="flex flex-col gap-5"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      {!saved.email_available ? (
        <Alert>
          <BellRing aria-hidden="true" />
          <AlertDescription>Email isn&apos;t set up on this server yet: choices for email are kept and apply once it is.</AlertDescription>
        </Alert>
      ) : null}
      <div className="overflow-x-auto">
        <Table>
          <caption className="sr-only">Where each kind of notification goes, besides the app</caption>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">Notifications</TableHead>
              <TableHead scope="col">Email</TableHead>
              <TableHead scope="col">Slack message</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {saved.categories.map((category) => {
              const channels = draft.channels[category.key];
              return (
                <TableRow key={category.key}>
                  <TableHead scope="row" className="py-2 align-middle font-normal whitespace-normal">
                    <span className="font-medium">{category.label}</span>
                    <span className="block text-xs text-muted-foreground">{category.description}</span>
                  </TableHead>
                  <TableCell className="align-middle">
                    <SimpleSelect<EmailMode>
                      size="sm"
                      ariaLabel={`Email for ${category.label}`}
                      value={channels.email}
                      disabled={pending}
                      onChange={(email) => setDraft((d) => setChannel(d, category.key, { email }))}
                      options={EMAIL_MODES.map((mode) => ({ value: mode, label: EMAIL_MODE_LABELS[mode] }))}
                    />
                  </TableCell>
                  <TableCell className="align-middle">
                    <Checkbox
                      aria-label={`Slack message for ${category.label}`}
                      checked={channels.slack}
                      disabled={pending || (!saved.slack_available && !channels.slack)}
                      onCheckedChange={(checked) => setDraft((d) => setChannel(d, category.key, { slack: checked === true }))}
                    />
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
      {!saved.slack_available ? (
        <p className="text-xs text-muted-foreground">Slack messages need your organization&apos;s admins to connect a Slack bot first (Admin › Automation).</p>
      ) : (
        <p className="text-xs text-muted-foreground">Slack finds you by your email address ({me.email}).</p>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Daily digest at" hint={usesDigest(draft) ? "Unread notifications since the last digest, once a day." : "Used when a category is set to the daily digest."}>
          {({ id, describedBy }) => (
            <SimpleSelect
              id={id}
              describedBy={describedBy}
              value={String(draft.digestHour)}
              disabled={pending}
              onChange={(hour) => setDraft((d) => ({ ...d, digestHour: Number(hour) }))}
              options={DIGEST_HOURS.map((hour) => ({ value: String(hour), label: hourLabel(hour) }))}
            />
          )}
        </Field>
        {canUseDirectory(me.org_role) ? (
          <BackupApprover
            value={draft.backupApproverId}
            meId={me.id}
            disabled={pending}
            onChange={(backupApproverId) => setDraft((d) => ({ ...d, backupApproverId }))}
          />
        ) : null}
      </div>

      {error ? <ErrorAlert error={error} /> : null}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground" aria-live="polite">
          {channelSummary(draft)}
        </p>
        <Button type="submit" disabled={pending || !dirty}>
          {pending ? "Saving…" : "Save notification settings"}
        </Button>
      </div>
    </form>
  );
}

function BackupApprover({
  value,
  meId,
  disabled,
  onChange,
}: {
  value: string | null;
  meId: string;
  disabled: boolean;
  onChange: (id: string | null) => void;
}) {
  const directory = usePeople(true);
  const [picking, setPicking] = useState(false);
  const person = value ? directory.byId.get(value) : null;
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-sm font-medium" id="backup-approver-label">
        Backup approver
      </span>
      {value ? (
        <div className="flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm" aria-labelledby="backup-approver-label">
          <UserRoundCheck className="size-4 text-muted-foreground" aria-hidden="true" />
          <span className="min-w-0 flex-1 truncate">{person ? `${person.display_name} (${person.email})` : "A colleague"}</span>
          <Button type="button" size="icon-sm" variant="ghost" aria-label="Remove the backup approver" disabled={disabled} onClick={() => onChange(null)}>
            <X aria-hidden="true" />
          </Button>
        </div>
      ) : picking ? (
        <ColleaguePicker
          autoFocus
          label="Find your backup approver"
          exclude={new Set([meId])}
          disabled={disabled}
          onPick={(picked) => {
            onChange(picked.id);
            setPicking(false);
          }}
        />
      ) : (
        <Button type="button" variant="outline" size="sm" className="w-fit" disabled={disabled} onClick={() => setPicking(true)}>
          Choose a colleague
        </Button>
      )}
      <p className="text-xs text-muted-foreground">
        When an approval waits for you too long, it also goes to them (your admins set how long). Without one, it goes to your department&apos;s managers, or the org admins.
      </p>
      {directory.error ? <p className="text-xs text-destructive">{errorMessage(directory.error).title}</p> : null}
    </div>
  );
}
