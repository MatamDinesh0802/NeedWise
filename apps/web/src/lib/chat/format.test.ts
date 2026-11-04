import { describe, expect, it } from "vitest";

import { formatCost, formatUsage, withheldNotice } from "./format";

describe("formatUsage", () => {
  it("formats tokens, cost and model", () => {
    expect(formatUsage({ model: "claude-opus-5-5", input_tokens: 2417, output_tokens: 339, cost_usd: 0.017 })).toBe(
      "2,417 in · 339 out · $0.017 · claude-opus-5-5",
    );
  });

  it("hides the cost when it is unknown (null)", () => {
    expect(formatUsage({ model: "qwen3:8b", input_tokens: 1200, output_tokens: 80, cost_usd: null })).toBe(
      "1,200 in · 80 out · qwen3:8b",
    );
  });

  it("returns null when no model was called (e.g. a guest with no sources)", () => {
    expect(formatUsage({ model: "", input_tokens: 0, output_tokens: 0, cost_usd: 0 })).toBeNull();
    expect(formatUsage({})).toBeNull();
    expect(formatUsage(null)).toBeNull();
  });

  it("works without a model name", () => {
    expect(formatUsage({ input_tokens: 10, output_tokens: 2, cost_usd: 0.0004 })).toBe("10 in · 2 out · <$0.001");
  });
});

describe("formatCost", () => {
  it.each([
    [0, "$0"],
    [0.0004, "<$0.001"],
    [0.017, "$0.017"],
    [0.1234, "$0.123"],
    [1.5, "$1.50"],
  ])("%f → %s", (cost, text) => {
    expect(formatCost(cost)).toBe(text);
  });
});

describe("withheldNotice", () => {
  it("explains withheld restricted sources, or says nothing", () => {
    expect(withheldNotice(0)).toBeNull();
    expect(withheldNotice(1)).toMatch(/^1 restricted source matched.*external AI models/);
    expect(withheldNotice(3)).toMatch(/^3 restricted sources matched/);
  });
});
