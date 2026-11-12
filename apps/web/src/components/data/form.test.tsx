import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Field, SimpleSelect } from "./form";

describe("SimpleSelect in a Field", () => {
  it("announces the field's hint and error with the select, and marks it invalid", () => {
    const html = renderToStaticMarkup(
      <Field label="Collection" hint="Everyone who can view it sees the meeting." error="Choose where the meeting goes.">
        {({ id, describedBy, invalid }) => (
          <SimpleSelect id={id} describedBy={describedBy} invalid={invalid} value="" onChange={() => {}} options={[{ value: "", label: "Choose" }]} />
        )}
      </Field>,
    );
    const trigger = html.match(/<button[^>]*>/)?.[0] ?? "";
    const describedBy = trigger.match(/aria-describedby="([^"]+)"/)?.[1].split(" ") ?? [];
    expect(describedBy).toHaveLength(2);
    for (const id of describedBy) expect(html).toContain(`id="${id}"`);
    expect(trigger).toContain('aria-invalid="true"');
  });
});
