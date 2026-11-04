/** Shared test data. Mirrors the /api/v1/me contract for the seeded demo users. */
export const alice = {
  id: "0190f2c4-0000-7000-8000-000000000001",
  email: "alice@acme.test",
  display_name: "Alice Anders",
  title: "HR Manager",
  org_role: "member",
  tenant: { id: "t1", slug: "acme", name: "Acme Robotics" },
  departments: [{ id: "d1", slug: "people", name: "People & HR", role: "manager" }],
  groups: [{ id: "g1", slug: "hr-records", name: "HR records" }],
} as const;
