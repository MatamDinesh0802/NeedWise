import { describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";
import {
  createTokenFlow,
  TOKEN_SCOPES,
  tokenStatus,
  toTokenRecord,
  validateTokenForm,
  type AccessToken,
  type CreatedAccessToken,
} from "./tokens";

const PLAIN = "wm_pat_s3cr3t-value-that-must-never-leak";

const created: CreatedAccessToken = {
  id: "t1",
  name: "Laptop CLI",
  prefix: "wm_pat_s3cr",
  scopes: ["profile:read"],
  created_at: "2026-11-04T06:00:00Z",
  expires_at: "2027-02-02T06:00:00Z",
  last_used_at: null,
  revoked_at: null,
  token: PLAIN,
};

const validInput = { name: "  Laptop CLI  ", scopes: ["profile:read"], expiresInDays: 90 };

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => (resolve = res));
  return { promise, resolve };
}

describe("validateTokenForm", () => {
  it("accepts a valid form", () => {
    expect(validateTokenForm(validInput)).toEqual({});
  });

  it("requires a name, at least one scope and 1–365 days", () => {
    expect(validateTokenForm({ name: "  ", scopes: [], expiresInDays: 0 })).toEqual({
      name: expect.any(String),
      scopes: expect.any(String),
      expiresInDays: expect.any(String),
    });
    expect(validateTokenForm({ ...validInput, expiresInDays: 366 }).expiresInDays).toBeDefined();
    expect(validateTokenForm({ ...validInput, expiresInDays: 1.5 }).expiresInDays).toBeDefined();
    expect(validateTokenForm({ ...validInput, name: "x".repeat(101) }).name).toMatch(/100/);
  });
});

describe("scopes", () => {
  it("offers the contract's scopes; only profile:read works today", () => {
    expect(TOKEN_SCOPES.map((s) => [s.value, s.active])).toEqual([
      ["profile:read", true],
      ["knowledge:read", false],
      ["assistant:ask", false],
    ]);
  });
});

describe("toTokenRecord / tokenStatus", () => {
  it("drops the plain token", () => {
    const record = toTokenRecord(created);
    expect(record).not.toHaveProperty("token");
    expect(JSON.stringify(record)).not.toContain(PLAIN);
    expect(record.name).toBe("Laptop CLI");
  });

  it("derives active / expired / revoked", () => {
    const now = new Date("2026-12-01T00:00:00Z");
    const base: AccessToken = toTokenRecord(created);
    expect(tokenStatus(base, now)).toBe("active");
    expect(tokenStatus({ ...base, expires_at: "2026-11-30T00:00:00Z" }, now)).toBe("expired");
    expect(tokenStatus({ ...base, revoked_at: "2026-11-20T00:00:00Z" }, now)).toBe("revoked");
  });
});

describe("show-once creation flow", () => {
  it("validates before calling the API", async () => {
    const create = vi.fn();
    const flow = createTokenFlow({ create });
    await flow.submit({ name: "", scopes: [], expiresInDays: 90 });
    expect(create).not.toHaveBeenCalled();
    expect(flow.getSnapshot()).toMatchObject({ step: "form", fieldErrors: { name: expect.any(String), scopes: expect.any(String) } });
  });

  it("creates, reveals the token once, and never hands it to the list", async () => {
    const pending = deferred<CreatedAccessToken>();
    const create = vi.fn(() => pending.promise);
    const onCreated = vi.fn();
    const flow = createTokenFlow({ create, onCreated });

    const submitted = flow.submit(validInput);
    expect(flow.getSnapshot()).toEqual({ step: "creating" });
    expect(create).toHaveBeenCalledWith({ name: "Laptop CLI", scopes: ["profile:read"], expires_in_days: 90 });

    pending.resolve(created);
    await submitted;
    const state = flow.getSnapshot();
    expect(state).toMatchObject({ step: "reveal", token: PLAIN, copy: "idle" });
    expect(state.step === "reveal" && state.record).not.toHaveProperty("token");

    // The list only ever receives metadata.
    expect(onCreated).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(onCreated.mock.calls)).not.toContain(PLAIN);
  });

  it("ignores a second submit while creating", async () => {
    const pending = deferred<CreatedAccessToken>();
    const create = vi.fn(() => pending.promise);
    const flow = createTokenFlow({ create });
    const first = flow.submit(validInput);
    await flow.submit(validInput);
    expect(create).toHaveBeenCalledTimes(1);
    pending.resolve(created);
    await first;
  });

  it("copies the token and reports success or failure", async () => {
    const writeClipboard = vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error("denied"));
    const flow = createTokenFlow({ create: async () => created, writeClipboard });
    await flow.submit(validInput);

    await flow.copy();
    expect(writeClipboard).toHaveBeenCalledWith(PLAIN);
    expect(flow.getSnapshot()).toMatchObject({ step: "reveal", copy: "copied" });

    await flow.copy();
    expect(flow.getSnapshot()).toMatchObject({ step: "reveal", copy: "failed" });
  });

  it("forgets the token for good on Done", async () => {
    const flow = createTokenFlow({ create: async () => created, writeClipboard: async () => {} });
    await flow.submit(validInput);
    flow.finish();

    const state = flow.getSnapshot();
    expect(state).toEqual({ step: "form", error: null, fieldErrors: {} });
    expect(JSON.stringify(state)).not.toContain(PLAIN);
    await flow.copy(); // nothing left to copy
    expect(flow.getSnapshot()).toEqual(state);
  });

  it("does not resurrect the token if a slow copy finishes after Done", async () => {
    const clip = deferred<void>();
    const flow = createTokenFlow({ create: async () => created, writeClipboard: () => clip.promise });
    await flow.submit(validInput);
    const copying = flow.copy();
    flow.finish();
    clip.resolve();
    await copying;
    expect(JSON.stringify(flow.getSnapshot())).not.toContain(PLAIN);
  });

  it("returns to the form with a friendly error when the API fails", async () => {
    const flow = createTokenFlow({
      create: async () => {
        throw new ApiError({ status: 403, title: "Not allowed", code: "forbidden" });
      },
    });
    await flow.submit(validInput);
    expect(flow.getSnapshot()).toMatchObject({
      step: "form",
      error: { title: "You don't have access", code: "forbidden" },
    });
  });

  it("notifies subscribers on every step", async () => {
    const flow = createTokenFlow({ create: async () => created });
    const steps: string[] = [];
    flow.subscribe(() => steps.push(flow.getSnapshot().step));
    await flow.submit(validInput);
    flow.finish();
    expect(steps).toEqual(["creating", "reveal", "form"]);
  });
});
