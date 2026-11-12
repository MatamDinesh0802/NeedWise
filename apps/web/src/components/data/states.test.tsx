import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { tenantBudget } from "@/test/usage-fixtures";

import { ErrorAlert } from "./states";

const budgetExceeded = new ApiError({
  status: 429,
  title: "Budget exceeded",
  code: "budget_exceeded",
  detail: "The organization's monthly budget is used up.",
  problem: { type: "about:blank", title: "Budget exceeded", status: 429, code: "budget_exceeded", budget: { ...tenantBudget, status: "exceeded", spent_usd: 500 } },
});

describe("ErrorAlert", () => {
  it("explains a used-up budget instead of a generic error, without a pointless retry", () => {
    // Insights (/analytics/ask), the agent test console and every other ErrorAlert get this.
    const html = renderToStaticMarkup(<ErrorAlert error={budgetExceeded} onRetry={() => {}} />);
    expect(html).toContain('data-code="budget_exceeded"');
    expect(html).toContain("The monthly organization budget is used up");
    expect(html).toContain("$500 of $500 spent (100%)");
    expect(html).toContain("Resets ");
    expect(html).toContain("Ask an owner or admin of your organization");
    expect(html).not.toContain("Retry");
  });

  it("keeps the usual alert for other errors", () => {
    const html = renderToStaticMarkup(<ErrorAlert error={new ApiError({ status: 500, title: "Server error" })} onRetry={() => {}} />);
    expect(html).toContain("Server error");
    expect(html).toContain("Retry");
  });
});
