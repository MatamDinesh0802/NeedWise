"use client";

import { Search } from "lucide-react";
import { useId, useState } from "react";

import { ErrorAlert } from "@/components/data/states";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiQuery } from "@/hooks/use-api-query";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { peopleApi, peopleKeys, personDetail, SEARCH_LIMIT, type Person } from "@/lib/people";

/**
 * Search the colleague directory (GET /people?q=, members only) and pick one person. For admin
 * screens, PersonPicker searches /admin/users instead.
 */
export function ColleaguePicker({
  onPick,
  exclude = new Set(),
  disabled,
  label = "Find a colleague",
  autoFocus,
}: {
  onPick: (person: Person) => void;
  exclude?: ReadonlySet<string>;
  disabled?: boolean;
  label?: string;
  autoFocus?: boolean;
}) {
  const id = useId();
  const [query, setQuery] = useState("");
  const q = useDebouncedValue(query.trim(), 250);
  const { state, refetch } = useApiQuery(q ? peopleKeys.search(q) : null, () => peopleApi.list(q, SEARCH_LIMIT));
  const results = state.status === "success" ? state.data.filter((p) => !exclude.has(p.id)) : [];

  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <div className="relative">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
        <Input
          id={id}
          type="search"
          value={query}
          autoFocus={autoFocus}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name or email"
          disabled={disabled}
          className="pl-8"
          aria-controls={`${id}-results`}
        />
      </div>
      <div id={`${id}-results`} aria-live="polite">
        {q === "" ? null : state.status === "loading" ? (
          <Skeleton className="h-16 w-full" />
        ) : state.status === "error" ? (
          <ErrorAlert error={state.error} onRetry={() => void refetch()} />
        ) : results.length === 0 ? (
          <p className="px-1 text-sm text-muted-foreground">No colleague matches “{q}”.</p>
        ) : (
          <ul className="flex max-h-56 flex-col overflow-y-auto rounded-lg border" aria-label="Matching colleagues">
            {results.map((person) => (
              <li key={person.id} className="border-b last:border-0">
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => {
                    onPick(person);
                    setQuery("");
                  }}
                  className="flex w-full flex-col items-start px-3 py-2 text-left text-sm hover:bg-muted focus-visible:bg-muted focus-visible:outline-none disabled:opacity-50"
                >
                  <span className="font-medium">{person.display_name}</span>
                  <span className="text-xs text-muted-foreground">
                    {person.email}
                    {personDetail(person) ? ` · ${personDetail(person)}` : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
