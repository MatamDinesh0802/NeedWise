import { describe, expect, it } from "vitest";

import { questionError, unavailableCopy, verifiedLabel, waitingMessage } from "./ask";

describe("questionError", () => {
  it("needs text, at most 1000 characters", () => {
    expect(questionError("  ")).toBe("Type a question first.");
    expect(questionError("Revenue by month")).toBeNull();
    expect(questionError("x".repeat(1001))).toContain("1,000");
  });
});

describe("waitingMessage", () => {
  it("sets expectations, then says it's slow, then suggests the history", () => {
    expect(waitingMessage(0).detail).toContain("10–30 seconds");
    expect(waitingMessage(45).title).toBe("Still working");
    expect(waitingMessage(120).detail).toContain("history");
  });
});

describe("unavailableCopy", () => {
  it("tells members who to ask and gives admins the setup link", () => {
    const notConfigured = { available: false as const, reason: "not_configured" as const, message: "" };
    expect(unavailableCopy(notConfigured, "member")).toMatchObject({ title: "Insights isn't set up yet", action: null });
    expect(unavailableCopy(notConfigured, "member").description).toContain("owner or admin");
    expect(unavailableCopy(notConfigured, "admin").action).toEqual({ href: "/admin/analytics", label: "Set up analytics" });

    const noAccess = { available: false as const, reason: "no_access" as const, message: "" };
    expect(unavailableCopy(noAccess, "member").description).toContain("Ask your manager or an administrator");
    expect(unavailableCopy(noAccess, "auditor").action).toBeNull();
    expect(unavailableCopy(noAccess, "owner").action?.href).toBe("/admin/analytics");
  });
});

describe("verifiedLabel", () => {
  it("only labels answers that state figures", () => {
    expect(verifiedLabel(3, 3)).toBe("3/3 figures verified");
    expect(verifiedLabel(0, 0)).toBeNull();
  });
});
