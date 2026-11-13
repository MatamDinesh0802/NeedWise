import { createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { initialProgress, reduceProgress } from "@/lib/analytics/progress";
import type { Me } from "@/lib/session";
import { alice } from "@/test/fixtures";
import { model } from "@/test/analytics-fixtures";

import { AskProgress } from "./ask-progress";
import { allExamples, Unavailable } from "./insights-view";
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
  it("is a busy region with a polite status, the steps so far and a way to stop", () => {
    let progress = reduceProgress(initialProgress(), { id: "1-1", type: "run.started", data: {} });
    progress = reduceProgress(progress, {
      id: "1-2",
      type: "analytics.step",
      data: { step: "query_compiled", sql: "SELECT 1", dataset: "uploads", dialect: "duckdb", purpose: "Revenue by month" },
    });
    const html = renderToStaticMarkup(<AskProgress question="Revenue by month" startedAt={Date.now()} progress={progress} onStop={() => {}} />);
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain('role="status" aria-live="polite"');
    expect(html).toContain("Query 1: Revenue by month: running.");
    expect(html).toContain('aria-label="Steps"');
    expect(html).toContain("Planning the queries<span class=\"sr-only\"> (Done)</span>");
    expect(html).toContain("Query 1: Revenue by month<span class=\"sr-only\"> (In progress)</span>");
    expect(html).toContain("Checking every figure against the results<span class=\"sr-only\"> (Not started)</span>");
    expect(html).toContain('aria-expanded="false"'); // the SQL, collapsed
    expect(html).toContain("“Revenue by month”");
    expect(html).toContain("Stop waiting");
  });
});

describe("allExamples", () => {
  it("offers the examples of every dataset once", () => {
    const two = {
      ...model,
      datasets: [
        { ...model, key: "uploads", kind: "files" as const, dialect: "duckdb" as const },
        { ...model, key: "shop", kind: "postgres" as const, dialect: "postgres" as const, examples: ["Shop revenue by region", ...model.examples] },
      ],
    };
    expect(allExamples(model)).toEqual(model.examples);
    expect(allExamples(two)).toEqual([...model.examples, "Shop revenue by region"].slice(0, 8));
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

  it("describes each dataset under its kind when there are several", () => {
    const two = {
      ...model,
      datasets: [
        { ...model, key: "uploads", kind: "files" as const, dialect: "duckdb" as const },
        { ...model, key: "shop", kind: "postgres" as const, dialect: "postgres" as const, name: "Shop orders", row_policy: null },
      ],
    };
    const html = renderToStaticMarkup(<ModelSummary model={two} />);
    expect(html).toContain("Uploaded tables");
    expect(html).toContain("PostgreSQL database");
    expect(html).toContain("Shop orders");
    expect(html).toContain('aria-labelledby="insights-model-shop"');
  });
});
