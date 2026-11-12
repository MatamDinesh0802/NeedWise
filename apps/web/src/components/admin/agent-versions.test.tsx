import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { AgentSnapshot, AgentVersion } from "@/lib/admin/agents";

import { VersionDiff, VersionList } from "./agent-versions";

const snapshot: AgentSnapshot = {
  key: "legal",
  name: "Legal agent",
  description: "Contracts and NDAs.",
  instructions: "",
  collections: ["legal-contracts"],
  examples: ["Who signs?"],
  tools: [],
  model: null,
  max_searches: 2,
  max_tool_calls: 0,
  max_output_tokens: 6000,
  enabled: true,
};

const versions: AgentVersion[] = [
  {
    version: 2,
    author: { id: "u1", name: "Erin Takahashi" },
    note: "Added NDA examples",
    created_at: "2026-11-12T10:00:00Z",
    current: true,
    snapshot: { ...snapshot, examples: ["Who signs?", "NDA without review?"] },
  },
  { version: 1, author: null, note: "", created_at: "2026-11-11T10:00:00Z", current: false, snapshot },
];

describe("VersionList", () => {
  it("lists versions newest first with author, time and note, as pressable buttons", () => {
    const html = renderToStaticMarkup(<VersionList versions={versions} selected={1} onSelect={() => {}} />);
    expect(html).toContain('aria-label="Versions, newest first"');
    expect(html.indexOf("Version 2")).toBeLessThan(html.indexOf("Version 1"));
    expect(html).toContain("Erin Takahashi");
    expect(html).toContain("System (seed or upgrade)"); // no author
    expect(html).toContain("Added NDA examples");
    expect(html).toContain('dateTime="2026-11-12T10:00:00Z"');
    expect(html).toMatch(/<button[^>]*aria-pressed="true"[^>]*>.*Version 1/);
    expect(html).toContain("Current");
  });
});

describe("VersionDiff", () => {
  it("shows only the fields that differ, before and after", () => {
    const html = renderToStaticMarkup(<VersionDiff version={versions[1].snapshot} current={versions[0].snapshot} />);
    expect(html).toContain('<th scope="row" class="px-3 py-2 text-left font-medium whitespace-nowrap">Example questions</th>');
    expect(html).toContain("<del");
    expect(html).toContain("Who signs?, NDA without review?");
    expect(html).not.toContain(">Name<");
  });

  it("says when nothing differs", () => {
    expect(renderToStaticMarkup(<VersionDiff version={snapshot} current={snapshot} />)).toContain(
      "No differences: this version matches the current definition.",
    );
  });
});
