import { createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Me } from "@/lib/session";
import { alice } from "@/test/fixtures";
import { model } from "@/test/analytics-fixtures";

import { AskProgress } from "./ask-progress";
import { Unavailable } from "./insights-view";
import { QuestionBox } from "./question-box";
import { ModelSummary } from "./side-panels";

const member = alice as unknown as Me;
const admin = { ...member, org_role: "admin" } as Me;

describe("Unavailable", () => {
  it("tells a member without access who to ask, without a setup link", () => {
    const html = renderToStaticMarkup(<Unavailable model={{ available: false, reason: "no_access", message: "" }} me={member} />);
    expect(html).toContain("You don&#x27;t have access to company data");
    expect(html).toContain("Ask your manager or an administrator");
    expect(html).not.toContain("/admin/analytics");
  });

  it("sends admins to the setup page when nothing is configured", () => {
    const html = renderToStaticMarkup(<Unavailable model={{ available: false, reason: "not_configured", message: "No data model yet." }} me={admin} />);
    expect(html).toContain("Insights isn&#x27;t set up yet");
    expect(html).toContain('href="/admin/analytics"');
    expect(html).toContain("Set up analytics");
    expect(html).toContain("No data model yet.");
  });
});

describe("QuestionBox", () => {
  it("has a labelled box and the model's examples as buttons", () => {
    const html = renderToStaticMarkup(
      <QuestionBox value="" onChange={() => {}} onAsk={() => {}} busy={false} examples={model.examples} inputRef={createRef()} />,
    );
    expect(html).toMatch(/<label for="([^"]+)"[^>]*>Ask a question about the data<\/label><textarea id="\1"/);
    expect(html).toContain('maxLength="1000"');
    expect(html).toContain('role="group" aria-label="Example questions"');
    expect(html).toContain("Win rate by region last quarter");
    expect(html).toMatch(/<button type="submit"[^>]*disabled/); // nothing to ask yet
  });

  it("locks while an answer is being made", () => {
    const html = renderToStaticMarkup(
      <QuestionBox value="Revenue by month" onChange={() => {}} onAsk={() => {}} busy examples={["x"]} inputRef={createRef()} />,
    );
    expect(html).toContain("Working…");
    expect(html).toMatch(/<textarea[^>]*disabled/);
  });
});

describe("AskProgress", () => {
  it("is a busy region with a polite status and a way to stop", () => {
    const html = renderToStaticMarkup(<AskProgress question="Revenue by month" startedAt={Date.now()} onStop={() => {}} />);
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain('role="status" aria-live="polite"');
    expect(html).toContain("Answers usually take 10–30 seconds");
    expect(html).toContain("“Revenue by month”");
    expect(html).toContain("Stop waiting");
  });
});

describe("ModelSummary", () => {
  it("states the data's date and the caller's row limits, and lists what can be asked", () => {
    const html = renderToStaticMarkup(<ModelSummary model={model} />);
    expect(html).toContain("Acme sales");
    expect(html).toContain("Data through Sep 30, 2026");
    expect(html).toContain("You see only the customers and deals you own.");
    expect(html).toContain("Metrics (2)");
    expect(html).toContain("Breakdowns (4)");
    expect(html).toContain("Over time by <span");
    expect(html).toContain("invoice_date, amount_eur");
  });
});
