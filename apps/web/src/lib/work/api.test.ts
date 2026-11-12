import { describe, expect, it } from "vitest";

import { approvalsPath, workKeys } from "./api";

describe("approvalsPath", () => {
  it("asks for one conversation's or one workflow run's approvals", () => {
    expect(approvalsPath("pending")).toBe("/api/v1/approvals?status=pending");
    expect(approvalsPath("all", { conversationId: "c1" })).toBe("/api/v1/approvals?status=all&conversation_id=c1");
    expect(approvalsPath("all", { workflowRunId: "run 1" })).toBe("/api/v1/approvals?status=all&workflow_run_id=run+1");
  });
});

describe("workKeys", () => {
  it("keeps a conversation's approvals under the prefix a decision refreshes", () => {
    expect(workKeys.conversationApprovals("c1").startsWith(`${workKeys.approvals}/`)).toBe(true);
  });
});
