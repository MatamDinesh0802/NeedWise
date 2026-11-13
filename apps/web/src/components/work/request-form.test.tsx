import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { RequestResult, Task } from "@/lib/work/api";

import { RequestSent } from "./request-form";

const task: Task = {
  id: "t-1",
  title: "Refund for invoice 1182",
  description: "",
  status: "open",
  assignee_id: null,
  department_id: "d-fin",
  created_by: "u-bob",
  due_at: null,
  origin: { via: "request" },
  created_at: "2026-11-13T09:00:00Z",
};

describe("RequestSent", () => {
  it("says where the request went, how and why, and links to it", () => {
    const result: RequestResult = {
      task,
      routing: {
        method: "rule",
        department: "finance",
        department_name: "Finance",
        confidence: 1,
        matched: ["“invoice”", "“refund”"],
        explanation: "Matched the Finance rule: “invoice”, “refund”.",
      },
    };
    const html = renderToStaticMarkup(<RequestSent result={result} meId="u-bob" onAnother={() => {}} />);
    expect(html).toContain("Sent to Finance");
    expect(html).toContain("Routed by a rule. Matched the Finance rule: “invoice”, “refund”.");
    expect(html).toContain('href="/tasks/t-1"');
    expect(html).toContain("Tasks › Created by me");
    expect(html).toContain('role="status"');
  });

  it("says when a person will route it", () => {
    const result: RequestResult = {
      task: { ...task, department_id: null, triage: true },
      routing: {
        method: "triage",
        department: null,
        department_name: null,
        confidence: 0.4,
        matched: [],
        explanation: "No rule matched. The model was not sure enough (no department; it needs 0.70). A person will route it.",
      },
    };
    const html = renderToStaticMarkup(<RequestSent result={result} meId="u-bob" onAnother={() => {}} />);
    expect(html).toContain("Sent. A person will route it.");
    expect(html).toContain("Waiting for a person to route it.");
  });
});
