import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { answer } from "@/test/analytics-fixtures";

import { AnswerView, VerificationNote } from "./answer-view";
import { refreshErrorText } from "./query-panel";

describe("AnswerView", () => {
  it("shows the chart, the checked narrative and every query with its SQL and results", () => {
    const html = renderToStaticMarkup(<AnswerView answer={answer()} />);
    expect(html).toContain("Monthly revenue trend this year and the top 3 drivers");
    expect(html).toContain('tabindex="-1"'); // the heading takes focus when an answer arrives
    expect(html).toContain("Monthly revenue by product line, 2026"); // chart title
    expect(html).toContain('data-verified="true"');
    expect(html).toContain('data-verified="false"');
    expect(html).toContain("4 of 5 figures in the text match the query results; 1 could not be matched.");
    expect(html).toContain("The 2 queries behind this answer");
    expect(html).toContain("Answers the question"); // Q1 is the main query
    expect(html).toContain("SELECT date_trunc(&#x27;month&#x27;, invoice_date)"); // main query's SQL is open
    expect(html).toContain("Copy SQL");
    expect(html).toContain("Refresh");
    expect(html).toContain("CSV");
    expect(html).toContain("€1,150,000"); // result cells, formatted
    expect(html).toContain('aria-sort="none"');
    expect(html).toContain("invoice_lines");
    expect(html).toContain("claude-sonnet-4-5 · 5,400 in / 820 out tokens · $0.029");
  });

  it("says plainly when the caller has no access", () => {
    const html = renderToStaticMarkup(
      <AnswerView answer={answer({ status: "no_access", error: "Only finance may query margins.", narrative: "", chart: null, queries: [], verification: { numbers: [], verified: 0, total: 0 } })} />,
    );
    expect(html).toContain("You don&#x27;t have access to the data this needs");
    expect(html).toContain("Only finance may query margins.");
    expect(html).toContain("No access");
    expect(html).not.toContain("<figure");
  });

  it("explains a failure and still shows the failed query", () => {
    const failed = answer({
      status: "failed",
      error: "The model could not write valid SQL.",
      narrative: "",
      chart: null,
      queries: [{ ...answer().queries[0], error: "Binder Error: column foo not found", rows: [], row_count: 0 }],
    });
    const html = renderToStaticMarkup(<AnswerView answer={failed} />);
    expect(html).toContain("This question couldn&#x27;t be answered");
    expect(html).toContain("The model could not write valid SQL.");
    expect(html).toContain("This query failed");
    expect(html).toContain("Binder Error: column foo not found");
  });

  it("says why there is no chart instead of drawing a wrong one", () => {
    const html = renderToStaticMarkup(<AnswerView answer={answer({ chart: { ...answer().chart!, y: ["margin"] } })} />);
    expect(html).toContain("No chart: The chart refers to “margin”");
    expect(html).not.toContain("<figure");
  });

  it("notes truncated results, next to the chart too", () => {
    const truncated = answer({ queries: [{ ...answer().queries[0], truncated: true }] });
    const html = renderToStaticMarkup(<AnswerView answer={truncated} />);
    expect(html).toContain("Only the first 6 rows are shown and in the CSV");
    expect(html).toContain("The chart shows only the first 6 rows of Q1");
    expect(renderToStaticMarkup(<AnswerView answer={answer()} />)).not.toContain("The chart shows only the first");
  });

  it("does not tell people to rephrase when the model was down", () => {
    const failed = (error: string) =>
      renderToStaticMarkup(<AnswerView answer={answer({ status: "failed", error, narrative: "", chart: null, queries: [] })} />);
    const down = failed("The language model is unavailable. Try again later.");
    expect(down).toContain("Try again later.");
    expect(down).not.toContain("Try rephrasing");
    expect(failed("Something went wrong while answering this question.")).toContain("Try rephrasing");
  });
});

describe("VerificationNote", () => {
  it("is calm when everything matched and says when there was nothing to check", () => {
    const all = renderToStaticMarkup(
      <VerificationNote verification={{ numbers: [{ text: "€5M", value: 5e6, verified: true, query: "Q1" }], verified: 1, total: 1 }} />,
    );
    expect(all).toContain('data-verified="all"');
    expect(all).toContain("The one figure in the text matches the query results.");
    expect(renderToStaticMarkup(<VerificationNote verification={{ numbers: [], verified: 0, total: 0 }} />)).toContain(
      "The text states no figures to check.",
    );
  });
});

describe("refreshErrorText", () => {
  it("explains a rejected SQL with the API's reason, and lost access", () => {
    expect(refreshErrorText(new ApiError({ status: 422, title: "x", code: "sql_rejected", detail: "Table products is not allowed." }))).toEqual({
      title: "The SQL was rejected",
      description: "Table products is not allowed.",
    });
    expect(refreshErrorText(new ApiError({ status: 403, title: "x", code: "no_access" })).title).toBe("You no longer have access");
    expect(refreshErrorText(new ApiError({ status: 0, title: "Network error", code: "network_error" })).title).toBe("Can't reach the server");
  });
});
