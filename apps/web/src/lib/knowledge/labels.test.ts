import { describe, expect, it } from "vitest";

import { formatBytes } from "@/lib/format";

import { fileTypeLabel, headingTrail, SCOPE_DESCRIPTIONS, SENSITIVITY_LABELS, TRUST_LABELS } from "./labels";

describe("headingTrail", () => {
  it("joins the heading path with ' > '", () => {
    expect(headingTrail(["2. Parental leave", "2.1 Duration"])).toBe("2. Parental leave > 2.1 Duration");
    expect(headingTrail([" Handbook ", "", "Intro"])).toBe("Handbook > Intro");
    expect(headingTrail([])).toBeNull();
  });
});

describe("labels", () => {
  it("explains every trust level, sensitivity and scope", () => {
    expect(TRUST_LABELS.official.description).toMatch(/Approved policy/);
    expect(TRUST_LABELS.discussion.description).toMatch(/not authoritative/i);
    expect(SENSITIVITY_LABELS.restricted.description).toMatch(/should only be seen by named people/);
    expect(SENSITIVITY_LABELS.restricted.description).toMatch(/sharing/);
    expect(SCOPE_DESCRIPTIONS.enterprise).toBe("Everyone in the organization except guests.");
    expect(SCOPE_DESCRIPTIONS.department).toBe("That department's members.");
    expect(SCOPE_DESCRIPTIONS.restricted).toBe("Only the people and groups you pick.");
  });

  it("names file types", () => {
    expect(fileTypeLabel("text/markdown")).toBe("Markdown");
    expect(fileTypeLabel("application/json")).toBe("Slack export (JSON)");
    expect(fileTypeLabel("application/x-unknown")).toBe("application/x-unknown");
  });
});

describe("formatBytes", () => {
  it.each([
    [0, "0 B"],
    [999, "999 B"],
    [1536, "1.5 KB"],
    [52_428_800, "50 MB"],
    [-1, "—"],
  ])("%i → %s", (bytes, text) => {
    expect(formatBytes(bytes)).toBe(text);
  });
});
