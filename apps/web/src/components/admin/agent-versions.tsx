"use client";

import { History, RotateCcw } from "lucide-react";
import { useState } from "react";

import { ConfirmDialog } from "@/components/data/confirm-dialog";
import { EmptyState, ErrorAlert, TableSkeleton } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { invalidateQueries, useApiQuery } from "@/hooks/use-api-query";
import {
  agentsApi,
  agentsKey,
  agentVersionsKey,
  diffSnapshots,
  restoreText,
  snapshotOf,
  type Agent,
  type AgentSnapshot,
  type AgentVersion,
} from "@/lib/admin/agents";
import { formatDateTime, formatRelative } from "@/lib/format";
import { toast } from "@/lib/stores/toasts";
import { cn } from "@/lib/utils";

/** The agent's saved versions, newest first, with a diff against the current definition. */
export function AgentVersionsPanel({ agent, onRestored }: { agent: Agent; onRestored: () => void }) {
  const versions = useApiQuery(agentVersionsKey(agent.key), () => agentsApi.versions(agent.key));
  const [picked, setPicked] = useState<number | null>(null);
  const [restoring, setRestoring] = useState<AgentVersion | null>(null);
  const confirm = restoring ? restoreText(agent, restoring.version) : null;

  if (versions.state.status === "loading") return <TableSkeleton rows={3} />;
  if (versions.state.status === "error") {
    return <ErrorAlert error={versions.state.error} onRetry={() => void versions.refetch()} />;
  }
  const items = versions.state.data.items;
  if (items.length === 0) {
    return <EmptyState icon={History} title="No versions yet" description="Saving the agent records its first version." />;
  }
  const selected = items.find((v) => v.version === picked) ?? items.find((v) => !v.current) ?? items[0];

  return (
    <div className="grid gap-4 md:grid-cols-[minmax(0,15rem)_minmax(0,1fr)]">
      <VersionList versions={items} selected={selected.version} onSelect={setPicked} />
      <section aria-labelledby="version-diff-heading" className="flex min-w-0 flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 id="version-diff-heading" className="text-sm font-medium">
            Version {selected.version} compared with the current definition (version {agent.version})
          </h4>
          {selected.current ? null : (
            <Button type="button" variant="outline" size="sm" onClick={() => setRestoring(selected)}>
              <RotateCcw data-icon="inline-start" aria-hidden="true" />
              Restore version {selected.version}
            </Button>
          )}
        </div>
        <VersionDiff version={selected.snapshot} current={snapshotOf(agent)} />
      </section>
      <ConfirmDialog
        open={restoring !== null}
        onOpenChange={(open) => !open && setRestoring(null)}
        title={confirm?.title ?? "Restore version?"}
        description={confirm?.description ?? ""}
        confirmLabel={confirm?.confirm ?? "Restore"}
        destructive={false}
        onConfirm={async () => {
          if (!restoring) return;
          const saved = await agentsApi.restore(agent.key, restoring.version);
          await invalidateQueries(agentsKey);
          toast({
            title: `${saved.name}: version ${restoring.version} restored`,
            description: `Saved as version ${saved.version}. Takes effect on the next question.`,
          });
          onRestored();
        }}
      />
    </div>
  );
}

export function VersionList({
  versions,
  selected,
  onSelect,
}: {
  versions: readonly AgentVersion[];
  selected: number;
  onSelect: (version: number) => void;
}) {
  return (
    <ol className="flex max-h-96 flex-col gap-1 overflow-y-auto" aria-label="Versions, newest first">
      {versions.map((v) => (
        <li key={v.version}>
          <button
            type="button"
            aria-pressed={v.version === selected}
            onClick={() => onSelect(v.version)}
            className={cn(
              "flex w-full flex-col gap-0.5 rounded-lg border px-3 py-2 text-left text-sm hover:bg-muted/50 focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none",
              v.version === selected && "border-primary bg-muted/60",
            )}
          >
            <span className="flex items-center gap-2 font-medium">
              Version {v.version}
              {v.current ? <Badge variant="secondary">Current</Badge> : null}
            </span>
            <span className="text-xs text-muted-foreground">
              {v.author ? v.author.name : "System (seed or upgrade)"} ·{" "}
              <time dateTime={v.created_at} title={formatDateTime(v.created_at)}>
                {formatRelative(v.created_at)}
              </time>
            </span>
            {v.note ? <span className="line-clamp-2 text-xs">{v.note}</span> : null}
          </button>
        </li>
      ))}
    </ol>
  );
}

export function VersionDiff({ version, current }: { version: AgentSnapshot; current: AgentSnapshot }) {
  const changes = diffSnapshots(version, current);
  if (changes.length === 0) {
    return <p className="rounded-lg border border-dashed p-3 text-sm text-muted-foreground">No differences: this version matches the current definition.</p>;
  }
  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-sm">
        <caption className="sr-only">Fields that differ</caption>
        <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">Field</th>
            <th scope="col" className="px-3 py-2 font-medium">In this version</th>
            <th scope="col" className="px-3 py-2 font-medium">Current</th>
          </tr>
        </thead>
        <tbody>
          {changes.map((c) => (
            <tr key={c.field} className="border-t align-top">
              <th scope="row" className="px-3 py-2 text-left font-medium whitespace-nowrap">{c.label}</th>
              <td className="px-3 py-2 break-words whitespace-pre-wrap text-muted-foreground">
                <del className="decoration-destructive/50">{c.before}</del>
              </td>
              <td className="px-3 py-2 break-words whitespace-pre-wrap">
                <ins className="no-underline">{c.after}</ins>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
