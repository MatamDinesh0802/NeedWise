import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { adminBudgets } from "@/test/usage-fixtures";

import {
  formFromBudgets,
  isDirty,
  LIMIT_ERROR,
  newRow,
  parseLimit,
  parseWarn,
  problemsFromError,
  toUpdate,
  validateBudgets,
  WARN_ERROR,
  type BudgetsForm,
} from "./budgets";

describe("formFromBudgets", () => {
  it("orders rows organization, departments, people and keeps every field", () => {
    const form = formFromBudgets(adminBudgets);
    expect(form.rows.map((r) => r.scope)).toEqual(["tenant", "department", "user"]);
    expect(form.rows[1]).toEqual({
      key: "b-finance",
      id: "b-finance",
      scope: "department",
      subject: { id: "d-finance", name: "Finance" },
      period: "month",
      limit: "100",
      warnPercent: "80",
      action: "warn",
    });
    expect(form.userDefault).toEqual({ enabled: true, period: "month", limit: "25", action: "block" });
  });

  it("starts the default cap switched off when there is none", () => {
    expect(formFromBudgets({ budgets: [], user_default: null })).toEqual({
      rows: [],
      userDefault: { enabled: false, period: "month", limit: "", action: "block" },
    });
  });
});

describe("parsing", () => {
  it.each([
    ["12.50", 12.5],
    ["$1,200", 1200],
    [" 3 ", 3],
    [".5", 0.5],
    ["0", null],
    ["-4", null],
    ["ten", null],
    ["", null],
  ])("limit %j → %s", (text, value) => {
    expect(parseLimit(text)).toBe(value);
  });

  it.each([
    ["80", 0.8],
    ["100%", 1],
    ["1", 0.01],
    ["0", null],
    ["101", null],
    ["", null],
  ])("warn %j → %s", (text, value) => {
    expect(parseWarn(text)).toBe(value);
  });
});

describe("validateBudgets", () => {
  it("accepts the saved budgets", () => {
    expect(validateBudgets(formFromBudgets(adminBudgets))).toEqual({});
  });

  it("flags limits, warning levels, missing people and duplicates on their rows", () => {
    const form = formFromBudgets(adminBudgets);
    const person = newRow("user", null);
    const duplicate = { ...newRow("tenant", null), limit: "50" };
    const broken: BudgetsForm = {
      rows: [{ ...form.rows[0], limit: "0", warnPercent: "150" }, duplicate, person],
      userDefault: { ...form.userDefault, limit: "" },
    };
    const errors = validateBudgets(broken);
    expect(errors[`${form.rows[0].key}.limit`]).toBe(LIMIT_ERROR);
    expect(errors[`${form.rows[0].key}.warn`]).toBe(WARN_ERROR);
    expect(errors[`${duplicate.key}.period`]).toBe("There is already a monthly budget for the organization. Change the period or remove one.");
    expect(errors[`${person.key}.subject`]).toBe("Choose a person.");
    expect(errors["default.limit"]).toBe(LIMIT_ERROR);
  });

  it("allows a daily and a monthly budget for the same subject", () => {
    const form = formFromBudgets(adminBudgets);
    const daily = { ...newRow("tenant", null, "day"), limit: "40" };
    expect(validateBudgets({ ...form, rows: [...form.rows, daily] })).toEqual({});
  });

  it("ignores the default cap's limit while it is switched off", () => {
    const form = formFromBudgets({ budgets: [], user_default: null });
    expect(validateBudgets(form)).toEqual({});
  });
});

describe("toUpdate", () => {
  it("sends every row as the contract's budget input, in dollars and 0–1 warning levels", () => {
    const { body, order } = toUpdate(formFromBudgets(adminBudgets));
    expect(body).toEqual({
      budgets: [
        { scope: "tenant", subject_id: null, period: "month", limit_usd: 500, warn_at: 0.8, action: "block" },
        { scope: "department", subject_id: "d-finance", period: "month", limit_usd: 100, warn_at: 0.8, action: "warn" },
        { scope: "user", subject_id: "0190f2c4-0000-7000-8000-000000000001", period: "day", limit_usd: 2, warn_at: 0.8, action: "block" },
      ],
      user_default: { period: "month", limit_usd: 25, action: "block" },
    });
    expect(order).toEqual(["b-tenant", "b-finance", "b-alice"]);
  });

  it("leaves department budgets out when the license doesn't include them", () => {
    const { body, order } = toUpdate(formFromBudgets(adminBudgets), { includeDepartments: false });
    expect(body.budgets.map((b) => b.scope)).toEqual(["tenant", "user"]);
    expect(order).toEqual(["b-tenant", "b-alice"]);
  });

  it("sends no default cap when it is switched off", () => {
    const form = formFromBudgets(adminBudgets);
    expect(toUpdate({ ...form, userDefault: { ...form.userDefault, enabled: false } }).body.user_default).toBeNull();
  });
});

describe("isDirty", () => {
  it("is false until something changes", () => {
    const form = formFromBudgets(adminBudgets);
    expect(isDirty(form, adminBudgets)).toBe(false);
    expect(isDirty({ ...form, rows: [{ ...form.rows[0], limit: "600" }, ...form.rows.slice(1)] }, adminBudgets)).toBe(true);
    expect(isDirty({ ...form, rows: [...form.rows, newRow("tenant", null, "day")] }, adminBudgets)).toBe(true);
  });

  it("ignores hidden fields of a switched-off default cap", () => {
    const saved = { budgets: [], user_default: null };
    const form = formFromBudgets(saved);
    expect(isDirty({ ...form, userDefault: { ...form.userDefault, limit: "9" } }, saved)).toBe(false);
  });
});

describe("problemsFromError", () => {
  const order = ["b-tenant", "b-finance"];
  const invalid = (errors: unknown[], detail: string | null = null) =>
    new ApiError({ status: 422, title: "Invalid", code: "validation_error", detail, problem: { type: "about:blank", title: "Invalid", status: 422, errors } });

  it("places FastAPI errors on the rows they are about", () => {
    const { fields, general } = problemsFromError(
      invalid([
        { loc: ["body", "budgets", 1, "limit_usd"], msg: "Input should be greater than 0" },
        { loc: ["body", "budgets", 0, "warn_at"], msg: "Input should be less than or equal to 1" },
        { loc: ["body", "user_default", "limit_usd"], msg: "Input should be greater than 0" },
        { loc: ["body", "budgets"], msg: "Duplicate budget" },
      ]),
      order,
    );
    expect(fields).toEqual({
      "b-finance.limit": "Input should be greater than 0",
      "b-tenant.warn": "Input should be less than or equal to 1",
      "default.limit": "Input should be greater than 0",
    });
    expect(general).toEqual(["budgets: Duplicate budget"]);
  });

  it("shows a business-rule 422 by its detail", () => {
    const error = new ApiError({ status: 422, title: "Unknown person", code: "unknown_subject", detail: "One of the people no longer exists." });
    expect(problemsFromError(error, order)).toEqual({ fields: {}, general: ["One of the people no longer exists."] });
  });

  it("ignores other errors", () => {
    expect(problemsFromError(new ApiError({ status: 500, title: "Oops" }), order)).toEqual({ fields: {}, general: [] });
  });
});
