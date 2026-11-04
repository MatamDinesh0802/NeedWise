"use client";

import { Search } from "lucide-react";
import { useState } from "react";

import { ErrorAlert } from "@/components/data/states";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiQuery } from "@/hooks/use-api-query";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { adminApi, adminKeys, type AdminUser } from "@/lib/admin/api";

/** Search people (GET /admin/users?q=) and pick one. `exclude` hides existing members. */
export function PersonPicker({
  onPick,
  exclude,
  disabled,
  label = "Find a person",
}: {
  onPick: (user: AdminUser) => void;
  exclude: ReadonlySet<string>;
  disabled?: boolean;
  label?: string;
}) {
  const [query, setQuery] = useState("");
  const q = useDebouncedValue(query.trim(), 250);
  const { state, refetch } = useApiQuery(q ? adminKeys.userSearch(q) : null, () =>
    adminApi.listUsers({ q, limit: 8 }),
  );

  const results = state.status === "success" ? state.data.items.filter((u) => !exclude.has(u.id)) : [];

  return (
    <div className="flex flex-col gap-2">
      <div className="relative">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name or email"
          aria-label={label}
          disabled={disabled}
          className="pl-8"
        />
      </div>
      {q === "" ? null : state.status === "loading" ? (
        <Skeleton className="h-16 w-full" />
      ) : state.status === "error" ? (
        <ErrorAlert error={state.error} onRetry={() => void refetch()} />
      ) : results.length === 0 ? (
        <p className="px-1 text-sm text-muted-foreground">No one else matches “{q}”.</p>
      ) : (
        <ul className="flex max-h-56 flex-col overflow-y-auto rounded-lg border" aria-label="Search results">
          {results.map((user) => (
            <li key={user.id} className="border-b last:border-0">
              <button
                type="button"
                disabled={disabled}
                onClick={() => {
                  onPick(user);
                  setQuery("");
                }}
                className="flex w-full flex-col items-start px-3 py-2 text-left text-sm hover:bg-muted focus-visible:bg-muted focus-visible:outline-none disabled:opacity-50"
              >
                <span className="font-medium">{user.display_name}</span>
                <span className="text-xs text-muted-foreground">{user.email}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
