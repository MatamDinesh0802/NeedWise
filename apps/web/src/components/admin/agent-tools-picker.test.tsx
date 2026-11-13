import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { AgentTool } from "@/lib/admin/agents";

import { AgentToolsPicker } from "./agent-tools-picker";

const tools: AgentTool[] = [
  {
    name: "send_email",
    description: "Send an email.",
    side_effect: "external",
    scope: "assistant:ask",
    source: "builtin",
    for_agents: false,
    not_for_agents_reason: "send_email has external side effects and needs a person's approval.",
  },
  {
    name: "draft_email",
    description: "Prepare an email draft.",
    side_effect: "read",
    scope: "assistant:ask",
    source: "builtin",
    for_agents: true,
    not_for_agents_reason: null,
  },
];

describe("AgentToolsPicker", () => {
  it("labels every checkbox and disables tools that need approval, saying why", () => {
    const html = renderToStaticMarkup(<AgentToolsPicker tools={tools} selected={[]} onChange={() => {}} />);
    expect(html).toContain("<legend");
    expect(html.indexOf("draft_email")).toBeLessThan(html.indexOf("send_email")); // usable first
    expect(html).toMatch(/<label for="agent-tool-draft_email"/);
    expect(html).toContain('<input disabled="" id="agent-tool-send_email"');
    expect(html).toMatch(/aria-disabled="true"[^>]*aria-describedby="agent-tool-send_email-reason"/);
    expect(html).toContain('aria-describedby="agent-tool-send_email-reason"');
    expect(html).toContain("Not available to agents: send_email has external side effects");
    expect(html).toContain("Reads only");
    expect(html).toContain("Leaves NeedWise");
  });

  it("flags a chosen tool that is no longer registered", () => {
    const html = renderToStaticMarkup(<AgentToolsPicker tools={tools} selected={["old_tool"]} onChange={() => {}} />);
    expect(html).toContain("No longer registered: old_tool.");
  });

  it("says when no tools are registered", () => {
    expect(renderToStaticMarkup(<AgentToolsPicker tools={[]} selected={[]} onChange={() => {}} />)).toContain("No tools are registered.");
  });
});
