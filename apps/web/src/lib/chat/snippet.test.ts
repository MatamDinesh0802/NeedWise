import { describe, expect, it } from "vitest";

import { snippetParts } from "./snippet";

describe("snippetParts", () => {
  it("turns a markdown table into rows, and keeps the text around it", () => {
    const text = "Table 2. Paid leave\n| Role | Paid leave |\n|---|---|\n| Primary caregiver | 12 weeks |\n| Secondary caregiver | 4 weeks |\nApplies from 2025.";
    expect(snippetParts(text)).toEqual([
      { kind: "text", text: "Table 2. Paid leave" },
      { kind: "table", header: ["Role", "Paid leave"], rows: [["Primary caregiver", "12 weeks"], ["Secondary caregiver", "4 weeks"]] },
      { kind: "text", text: "Applies from 2025." },
    ]);
  });

  it("leaves text with pipes but no table rule alone", () => {
    expect(snippetParts("a | b\n| not | a table |")).toEqual([{ kind: "text", text: "a | b\n| not | a table |" }]);
  });
});
