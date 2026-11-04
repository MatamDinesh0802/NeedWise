import { describe, expect, it } from "vitest";

import {
  authErrorMessage,
  KNOWN_AUTH_ERROR_CODES,
  readAuthError,
  stripAuthErrorParam,
} from "./auth-errors";

const CONTRACT_CODES = [
  "login_expired",
  "login_cancelled",
  "login_failed",
  "login_unavailable",
  "no_organization",
  "account_disabled",
  "email_not_verified",
  "account_conflict",
];

describe("authErrorMessage", () => {
  it("covers every code in the API contract", () => {
    expect([...KNOWN_AUTH_ERROR_CODES].sort()).toEqual([...CONTRACT_CODES].sort());
  });

  it.each(CONTRACT_CODES)("gives %s its own friendly message", (code) => {
    const message = authErrorMessage(code);
    expect(message?.code).toBe(code);
    expect(message?.title).not.toBe("Sign-in problem");
    expect(message?.description.length).toBeGreaterThan(20);
  });

  it("gives every known code a distinct title", () => {
    const titles = CONTRACT_CODES.map((code) => authErrorMessage(code)?.title);
    expect(new Set(titles).size).toBe(CONTRACT_CODES.length);
  });

  it("maps specific codes to the expected wording", () => {
    expect(authErrorMessage("no_organization")?.description).toMatch(/administrator/i);
    expect(authErrorMessage("login_cancelled")?.title).toBe("Sign-in cancelled");
  });

  it("falls back to a generic message for unknown codes", () => {
    expect(authErrorMessage("something_new")).toEqual({
      code: "something_new",
      title: "Sign-in problem",
      description: expect.stringContaining("try again"),
    });
  });

  it("does not echo codes that look unsafe", () => {
    expect(authErrorMessage("<script>alert(1)</script>")).toMatchObject({ code: null, title: "Sign-in problem" });
    expect(authErrorMessage("x".repeat(65))?.code).toBeNull();
  });

  it("is not fooled by Object prototype keys", () => {
    expect(authErrorMessage("constructor")?.title).toBe("Sign-in problem");
    expect(authErrorMessage("__proto__")?.title).toBe("Sign-in problem");
  });

  it.each([null, undefined, "", "   "])("returns null for %j", (code) => {
    expect(authErrorMessage(code)).toBeNull();
  });
});

describe("readAuthError / stripAuthErrorParam", () => {
  it("reads the code with or without a leading question mark", () => {
    expect(readAuthError("?auth_error=login_failed")).toBe("login_failed");
    expect(readAuthError("tab=1&auth_error=no_organization")).toBe("no_organization");
    expect(readAuthError("?tab=1")).toBeNull();
  });

  it("removes only the auth error flag and keeps everything else", () => {
    expect(stripAuthErrorParam("http://localhost:8480/?auth_error=login_failed")).toBe("/");
    expect(stripAuthErrorParam("http://localhost:8480/knowledge?tab=docs&auth_error=x#top")).toBe(
      "/knowledge?tab=docs#top",
    );
    expect(stripAuthErrorParam("/admin?q=a%20b")).toBe("/admin?q=a+b");
  });
});
