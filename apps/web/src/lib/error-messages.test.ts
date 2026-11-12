import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { errorMessage, fieldErrors } from "./error-messages";

const api = (status: number, code: string | null, title = "Problem", extra: Record<string, unknown> = {}) =>
  new ApiError({
    status,
    code,
    title,
    detail: (extra.detail as string) ?? null,
    problem: { type: "about:blank", title, status, code: code ?? undefined, ...extra },
  });

describe("errorMessage", () => {
  it.each([
    [409, "email_taken", "Email already in use"],
    [409, "slug_taken", "Slug already in use"],
    [409, "last_owner", "The organization needs an owner"],
    [409, "group_cycle", "Groups can't contain each other"],
    [403, "owner_required", "Only an owner can do this"],
    [403, "self_change", "You can't change your own account here"],
    [403, "forbidden", "You don't have access"],
    [403, "csrf_header", "Request blocked"],
    [401, "unauthenticated", "You're signed out"],
    [404, "not_found", "Not found"],
    [0, "network_error", "Can't reach the server"],
  ])("%i %s → %s", (status, code, title) => {
    const message = errorMessage(api(status, code));
    expect(message.title).toBe(title);
    expect(message.code).toBe(code);
    expect(message.description.length).toBeGreaterThan(20);
  });

  it("explains group cycles in plain words", () => {
    expect(errorMessage(api(409, "group_cycle")).description).toMatch(/loop/);
  });

  it("summarizes validation errors by field", () => {
    const error = api(422, "validation_error", "Invalid request", {
      errors: [
        { type: "string_pattern_mismatch", loc: ["body", "email"], msg: "String should match pattern '^[^@\\s]+@'" },
        { type: "string_too_short", loc: ["body", "display_name"], msg: "String should have at least 1 character" },
      ],
    });
    expect(errorMessage(error)).toMatchObject({
      title: "Check the highlighted fields",
      code: "validation_error",
      description: "email: Enter a valid email address. display_name: String should have at least 1 character",
    });
  });

  it("falls back to the API's title and detail for unknown codes", () => {
    expect(errorMessage(api(409, "brand_new_code", "Something conflicted", { detail: "Details here." }))).toEqual({
      title: "Something conflicted",
      description: "Details here.",
      code: "brand_new_code",
    });
  });

  it("gives a generic retry message for server errors without detail", () => {
    expect(errorMessage(api(500, null, "Internal Server Error")).description).toMatch(/server/i);
  });

  it("handles non-API errors", () => {
    expect(errorMessage(new Error("boom"))).toEqual({ title: "Something went wrong", description: "boom", code: null });
    expect(errorMessage("weird").description).toBe("Please try again.");
  });

  it("is not fooled by prototype keys", () => {
    expect(errorMessage(api(400, "constructor", "Bad")).title).toBe("Bad");
  });
});

describe("fieldErrors", () => {
  it("maps FastAPI locations to field names with friendly pattern hints", () => {
    const error = api(422, "validation_error", "Invalid request", {
      errors: [
        { type: "string_pattern_mismatch", loc: ["body", "slug"], msg: "String should match pattern" },
        { type: "missing", loc: ["body", "name"], msg: "Field required" },
        { type: "int_parsing", loc: ["query", "limit"], msg: "Input should be a valid integer" },
        { type: "value_error", loc: [], msg: "Bad body" },
        "not-an-object",
      ],
    });
    expect(fieldErrors(error)).toEqual([
      { field: "slug", message: "Use only lowercase letters, numbers and single hyphens." },
      { field: "name", message: "Field required" },
      { field: "limit", message: "Input should be a valid integer" },
      { field: "request", message: "Bad body" },
    ]);
  });

  it("is empty for other errors", () => {
    expect(fieldErrors(api(409, "slug_taken"))).toEqual([]);
    expect(fieldErrors(new Error("x"))).toEqual([]);
    expect(fieldErrors(api(422, "validation_error"))).toEqual([]);
  });
});

describe("budget_exceeded", () => {
  const budget = {
    id: "b1",
    scope: "department",
    subject: { id: "d1", name: "Finance" },
    period: "month",
    limit_usd: 100,
    spent_usd: 101,
    warn_at: 0.8,
    status: "exceeded",
    resets_at: "2026-12-01T00:00:00Z",
  };

  it("names the budget, when it resets and who to ask, for toasts and plain alerts", () => {
    const message = errorMessage(api(429, "budget_exceeded", "Budget exceeded", { detail: "Finance's budget is used up.", budget }));
    expect(message.title).toBe("The monthly budget for Finance is used up");
    expect(message.description).toMatch(
      /^\$101 of \$100 spent \(101%\)\. Requests that use an AI model are paused until it resets\. Resets .+\. Ask your department manager/,
    );
    expect(message.code).toBe("budget_exceeded");
  });

  it("uses the API's words when the budget isn't included", () => {
    const message = errorMessage(api(429, "budget_exceeded", "Budget exceeded", { detail: "A budget is used up." }));
    expect(message.title).toBe("A usage budget is used up");
    expect(message.description).toBe("A budget is used up. Ask an owner or admin of your organization if you need more.");
  });
});
