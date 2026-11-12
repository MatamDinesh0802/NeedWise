import { describe, expect, it } from "vitest";

import { meetingsKeys } from "@/lib/meetings/api";
import { workKeys, type Task } from "@/lib/work/api";

import { assigneeText, canClaim, canSeeTask, dueText, emptyTaskForm, sortTasks, TASK_CHANGE_KEYS, taskOrigin, toTaskCreate, validateTaskForm } from "./tasks";

const task = (overrides: Partial<Task> = {}): Task => ({
  id: "t1",
  title: "Restock AMR spare parts",
  description: "",
  status: "open",
  assignee_id: null,
  department_id: null,
  created_by: "u-me",
  due_at: null,
  origin: { via: "web" },
  created_at: "2026-11-04T08:00:00Z",
  ...overrides,
});

describe("tasks", () => {
  it("refreshes My commitments, not only the task lists, after a task changes", () => {
    expect(TASK_CHANGE_KEYS).toEqual([workKeys.tasks, meetingsKeys.commitments]);
  });

  it("says who a task is for", () => {
    const ctx = { meId: "u-me", departments: new Map([["d-ops", { name: "Operations" }]]) };
    expect(assigneeText(task({ assignee_id: "u-me" }), ctx)).toBe("You");
    expect(assigneeText(task({ assignee_id: "u-bob" }), ctx)).toBe("Someone else");
    expect(assigneeText(task({ assignee_id: "u-bob" }), { ...ctx, people: new Map([["u-bob", { display_name: "Bob Berg" }]]) })).toBe("Bob Berg");
    expect(assigneeText(task({ department_id: "d-ops" }), ctx)).toBe("Operations queue");
    expect(assigneeText(task({ department_id: "d-x" }), ctx)).toBe("A team queue");
    expect(assigneeText(task(), ctx)).toBe("Unassigned");
  });

  it("describes due dates relative to today (UTC days)", () => {
    const now = new Date("2026-11-04T10:00:00Z");
    expect(dueText("2026-11-04T17:00:00Z", now)).toEqual({ text: "Due today", overdue: false });
    expect(dueText("2026-11-05T17:00:00Z", now)).toEqual({ text: "Due tomorrow", overdue: false });
    expect(dueText("2026-11-03T17:00:00Z", now)?.overdue).toBe(true);
    expect(dueText(null, now)).toBeNull();
  });

  it("validates the create form", () => {
    expect(validateTaskForm(emptyTaskForm())).toEqual({ title: "Add a title." });
    expect(validateTaskForm({ ...emptyTaskForm(), title: "x", assignTo: "queue" })).toEqual({ department: "Choose a team." });
    expect(validateTaskForm({ ...emptyTaskForm(), title: "x", assignTo: "person" })).toEqual({ personId: "Choose a person." });
  });

  it("builds the payload for each kind of assignment", () => {
    const base = { ...emptyTaskForm(), title: "  Restock  ", description: " Order 20 wheels ", due: "2026-11-10" };
    expect(toTaskCreate(base, "u-me")).toEqual({ title: "Restock", description: "Order 20 wheels", assignee_id: "u-me", due: "2026-11-10" });
    expect(toTaskCreate({ ...base, assignTo: "queue", department: "operations", due: "" }, "u-me")).toEqual({
      title: "Restock",
      description: "Order 20 wheels",
      department: "operations",
    });
    expect(toTaskCreate({ ...base, assignTo: "person", personId: "u-bob" }, "u-me")).toMatchObject({ assignee_id: "u-bob" });
    expect(toTaskCreate({ ...base, assignTo: "nobody", due: "" }, "u-me")).toEqual({ title: "Restock", description: "Order 20 wheels" });
  });

  it("sorts open tasks by due date first, finished ones last", () => {
    const sorted = sortTasks([
      task({ id: "done", status: "done", due_at: "2026-11-01T17:00:00Z" }),
      task({ id: "later", due_at: "2026-11-20T17:00:00Z" }),
      task({ id: "none" }),
      task({ id: "soon", status: "in_progress", due_at: "2026-11-05T17:00:00Z" }),
    ]);
    expect(sorted.map((t) => t.id)).toEqual(["soon", "later", "none", "done"]);
  });
});

describe("one task", () => {
  const me = { id: "u-me", departments: [{ id: "d-ops" }] };

  it("says where it came from, linking back to its meeting", () => {
    expect(taskOrigin(task({ origin: { via: "meeting", meeting_id: "m 1", action_index: 0 } }))).toEqual({
      kind: "meeting",
      label: "From a meeting",
      href: "/meetings/m%201",
    });
    expect(taskOrigin(task({ origin: { via: "assistant" } }))?.label).toBe("From the assistant");
    expect(taskOrigin(task({ origin: { via: "workflow", workflow_run_id: "r1" } }))?.href).toBeNull();
    expect(taskOrigin(task({ origin: { via: "web" } }))).toBeNull();
  });

  it("is visible to its assignee, its creator and its team queue, like the API's rule", () => {
    expect(canSeeTask(task({ assignee_id: "u-me", created_by: "u-other" }), me)).toBe(true);
    expect(canSeeTask(task({ assignee_id: "u-other", created_by: "u-me" }), me)).toBe(true);
    expect(canSeeTask(task({ assignee_id: "u-other", created_by: "u-other", department_id: "d-ops" }), me)).toBe(true);
    expect(canSeeTask(task({ assignee_id: "u-other", created_by: "u-other", department_id: "d-hr" }), me)).toBe(false);
    expect(canSeeTask(task({ assignee_id: "u-other", created_by: null }), me)).toBe(false);
  });

  it("can be claimed only while open, unassigned and in one of my team queues", () => {
    expect(canClaim(task({ department_id: "d-ops" }), me)).toBe(true);
    expect(canClaim(task({ department_id: "d-ops", assignee_id: "u-other" }), me)).toBe(false);
    expect(canClaim(task({ department_id: "d-hr" }), me)).toBe(false);
    expect(canClaim(task({ department_id: "d-ops", status: "done" }), me)).toBe(false);
    expect(canClaim(task(), me)).toBe(false);
  });
});
