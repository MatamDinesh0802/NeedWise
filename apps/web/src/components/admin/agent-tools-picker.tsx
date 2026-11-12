"use client";

import { useId } from "react";

import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { SIDE_EFFECT_LABEL, sortTools, type AgentTool } from "@/lib/admin/agents";

/**
 * Which registry tools the agent may call besides searching. Only read-level tools can be
 * chosen: the others are listed, disabled, with the reason (an agent cannot wait for approval).
 */
export function AgentToolsPicker({
  tools,
  selected,
  onChange,
  error,
}: {
  tools: readonly AgentTool[];
  selected: readonly string[];
  onChange: (tools: string[]) => void;
  error?: string | null;
}) {
  const hintId = useId();
  const known = new Set(tools.map((t) => t.name));
  const missing = selected.filter((name) => !known.has(name));
  return (
    <fieldset className="flex flex-col gap-2" aria-describedby={hintId}>
      <legend className="mb-1 text-sm font-medium">Tools</legend>
      <p id={hintId} className="-mt-1 text-xs text-muted-foreground">
        Besides searching its collections, the agent may call these. They run as the person asking, with that
        person&apos;s permissions. Tools that change something need a person&apos;s approval, so agents can&apos;t have them.
      </p>
      {tools.length === 0 ? (
        <p className="rounded-lg border border-dashed p-3 text-sm text-muted-foreground">No tools are registered.</p>
      ) : (
        <ul className="flex max-h-56 flex-col gap-1 overflow-y-auto rounded-lg border p-2">
          {sortTools(tools).map((tool) => {
            const id = `agent-tool-${tool.name}`;
            const reasonId = `${id}-reason`;
            const checked = selected.includes(tool.name);
            return (
              <li key={tool.name} className="flex items-start gap-2 rounded-md px-1.5 py-1 hover:bg-muted/50">
                <Checkbox
                  id={id}
                  className="mt-0.5"
                  checked={checked}
                  disabled={!tool.for_agents && !checked}
                  aria-describedby={tool.for_agents ? undefined : reasonId}
                  onCheckedChange={(next) =>
                    onChange(next ? [...selected, tool.name] : selected.filter((name) => name !== tool.name))
                  }
                />
                <div className="min-w-0 flex-1">
                  <label htmlFor={id} className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="font-mono">{tool.name}</span>
                    <Badge variant={tool.side_effect === "read" ? "secondary" : "outline"}>
                      {SIDE_EFFECT_LABEL[tool.side_effect] ?? tool.side_effect}
                    </Badge>
                  </label>
                  <p className="text-xs text-muted-foreground">{tool.description}</p>
                  {tool.for_agents ? null : (
                    <p id={reasonId} className="text-xs text-muted-foreground">
                      Not available to agents: {tool.not_for_agents_reason}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {missing.length > 0 ? (
        <p className="text-xs text-destructive">
          No longer registered: {missing.join(", ")}. Remove {missing.length === 1 ? "it" : "them"} before saving.{" "}
          <button type="button" className="underline" onClick={() => onChange(selected.filter((name) => known.has(name)))}>
            Remove
          </button>
        </p>
      ) : null}
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </fieldset>
  );
}
