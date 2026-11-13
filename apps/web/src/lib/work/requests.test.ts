import { describe, expect, it } from "vitest";

import { destination, emptyRequestForm, isRequest, routingLine, toRequestCreate, validateRequestForm } from "./requests";

describe("the request form", () => {
  it("needs a title and a real date, and sends only what was filled in", () => {
    expect(validateRequestForm(emptyRequestForm())).toEqual({ title: "Say in a few words what you need." });
    expect(validateRequestForm({ ...emptyRequestForm(), title: "x".repeat(201) }).title).toBe("At most 200 characters.");
    expect(validateRequestForm({ ...emptyRequestForm(), title: "VPN", due: "soon" }).due).toBe("Pick a date.");
    expect(toRequestCreate({ title: "  VPN drops  ", description: " At home. ", category: "", due: "" })).toEqual({
      title: "VPN drops",
      description: "At home.",
    });
    expect(toRequestCreate({ title: "Card", description: "", category: "Billing and expenses", due: "2026-11-20" })).toEqual({
      title: "Card",
      description: "",
      category: "Billing and expenses",
      due: "2026-11-20",
    });
  });
});

describe("routing decisions", () => {
  it("say where a request went and how", () => {
    expect(destination({ department: "finance", department_name: "Finance" })).toBe("Finance");
    expect(destination({ department: null, department_name: null })).toBe("Triage");
    expect(routingLine({ method: "rule", confidence: 1 })).toBe("Routed by a rule");
    expect(routingLine({ method: "model", confidence: 0.857 })).toBe("Routed by the model (confidence 0.86)");
    expect(routingLine({ method: "triage", confidence: 0.4 })).toBe("Waiting for a person to route it");
    expect(routingLine({ method: "human", confidence: null })).toBe("Routed by a person");
  });

  it("tell requests from other tasks", () => {
    expect(isRequest({ routing: null })).toBe(false);
    expect(isRequest({})).toBe(false);
    expect(
      isRequest({
        routing: { method: "rule", department: "hr", department_name: "HR", confidence: 1, matched: [], explanation: "" },
      }),
    ).toBe(true);
  });
});
