"use client";

import { Field } from "@/components/data/form";
import { Input } from "@/components/ui/input";
import { SLUG_MAX_LENGTH, slugError, suggestSlug } from "@/lib/slug";

export interface NameSlugValue {
  name: string;
  slug: string;
  /** True once the user typed a slug themselves; until then it follows the name. */
  slugEdited: boolean;
}

export const EMPTY_NAME_SLUG: NameSlugValue = { name: "", slug: "", slugEdited: false };

export function nameSlugErrors(value: NameSlugValue, serverSlugError?: string | null) {
  return {
    name: value.name.trim() ? null : "Enter a name.",
    slug: serverSlugError ?? (value.slug ? slugError(value.slug) : "Enter a value."),
  };
}

/** Name + slug inputs; the slug is suggested from the name until edited. */
export function NameSlugFields({
  value,
  onChange,
  serverSlugError,
  showErrors,
  slugLabel = "Slug",
}: {
  value: NameSlugValue;
  onChange: (value: NameSlugValue) => void;
  serverSlugError?: string | null;
  showErrors: boolean;
  slugLabel?: string;
}) {
  const errors = nameSlugErrors(value, serverSlugError);
  return (
    <>
      <Field label="Name" error={showErrors ? errors.name : null}>
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            required
            maxLength={200}
            value={value.name}
            onChange={(e) => {
              const name = e.target.value;
              onChange({ ...value, name, slug: value.slugEdited ? value.slug : suggestSlug(name) });
            }}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
          />
        )}
      </Field>
      <Field
        label={slugLabel}
        hint="Used in links and integrations. Lowercase letters, numbers and hyphens; can't be changed later."
        error={showErrors || value.slugEdited || serverSlugError ? errors.slug : null}
      >
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            required
            maxLength={SLUG_MAX_LENGTH}
            spellCheck={false}
            autoCapitalize="none"
            className="font-mono"
            value={value.slug}
            onChange={(e) => onChange({ ...value, slug: e.target.value, slugEdited: true })}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
          />
        )}
      </Field>
    </>
  );
}
