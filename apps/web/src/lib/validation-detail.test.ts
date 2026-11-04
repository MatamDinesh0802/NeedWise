import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";

import { describePath, detailErrors, parseValidationDetail } from "./validation-detail";

const EMAIL_DETAIL = `2 validation errors for SendEmail
to.0
  String should match pattern '^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$' [type=string_pattern_mismatch, input_value='bob', input_type=str]
    For further information visit https://errors.pydantic.dev/2.11/v/string_pattern_mismatch
subject
  String should have at least 1 character [type=string_too_short, input_value='', input_type=str]
    For further information visit https://errors.pydantic.dev/2.11/v/string_too_short`;

describe("parseValidationDetail", () => {
  it("turns Pydantic's text into one error per location", () => {
    expect(parseValidationDetail(EMAIL_DETAIL)).toEqual([
      { path: "to.0", field: "to", message: "Not in the expected format." },
      { path: "subject", field: "subject", message: "String should have at least 1 character" },
    ]);
  });

  it("drops the 'Value error,' prefix of model validators", () => {
    const detail = `1 validation error for Definition
steps.1
  Value error, step send: give either \`research\` or \`tool\` [type=value_error, input_value={'id': 'send'}, input_type=dict]`;
    expect(parseValidationDetail(detail)).toEqual([
      { path: "steps.1", field: "steps", message: "Step send: give either `research` or `tool`" },
    ]);
  });

  it("survives a detail cut off mid-line (the API keeps the first 500–800 characters)", () => {
    const cut = `1 validation error for WebConfig\nstart_urls.0\n  Input should be a valid URL, relative URL without a base [type=url_parsing, input_va`;
    expect(parseValidationDetail(cut)).toEqual([
      { path: "start_urls.0", field: "start_urls", message: "Input should be a valid URL, relative URL without a base" },
    ]);
  });

  it("keeps plain messages as one general error", () => {
    expect(parseValidationDetail("unknown source kind 'ftp'")).toEqual([{ path: "", field: "", message: "Unknown source kind 'ftp'" }]);
    expect(parseValidationDetail(null)).toEqual([]);
    expect(parseValidationDetail("   ")).toEqual([]);
  });
});

describe("describePath", () => {
  it.each([
    ["steps.1.tool", "Step 2 › tool"],
    ["to.0", "To 1"],
    ["trigger.schedule", "trigger › schedule"],
    ["", ""],
  ])("%j → %j", (path, text) => {
    expect(describePath(path)).toBe(text);
  });
});

describe("detailErrors", () => {
  it("only reads the codes it was asked for", () => {
    const error = new ApiError({ status: 422, title: "Invalid", code: "invalid_input", detail: EMAIL_DETAIL });
    expect(detailErrors(error, ["invalid_input"])).toHaveLength(2);
    expect(detailErrors(error, ["invalid_workflow"])).toEqual([]);
    expect(detailErrors(new Error("x"), ["invalid_input"])).toEqual([]);
  });
});
