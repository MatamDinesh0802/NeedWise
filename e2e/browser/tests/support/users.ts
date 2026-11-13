/**
 * Demo logins from seed/acme/company.yaml. Every one signs in with the demo password
 * (DEMO_PASSWORD, default "needwise-demo") through the real Keycloak form.
 */

export const DEMO_PASSWORD = process.env.DEMO_PASSWORD ?? "needwise-demo";

export type OrgRole = "owner" | "admin" | "auditor" | "member" | "guest";

export interface DemoUser {
  /** Short name, used for the cached sign-in file. */
  key: string;
  email: string;
  name: string;
  orgRole: OrgRole;
  /** The role badge in the account menu. */
  roleLabel: string;
  /** Departments in the account menu: [name, role there]. */
  departments: readonly (readonly [string, string])[];
}

export const USERS = {
  erin: {
    key: "erin",
    email: "erin@acme.test",
    name: "Erin Takahashi",
    orgRole: "admin",
    roleLabel: "Admin",
    departments: [["Operations", "Member"]],
  },
  hannah: {
    key: "hannah",
    email: "hannah.schmidt@acme.test",
    name: "Hannah Schmidt",
    orgRole: "member",
    roleLabel: "Member",
    departments: [["Finance", "Manager"]],
  },
  alice: {
    key: "alice",
    email: "alice@acme.test",
    name: "Alice Moreau",
    orgRole: "member",
    roleLabel: "Member",
    departments: [["People & HR", "Manager"]],
  },
  bob: {
    key: "bob",
    email: "bob@acme.test",
    name: "Bob Okafor",
    orgRole: "member",
    roleLabel: "Member",
    departments: [["Engineering", "Member"]],
  },
  mallory: {
    key: "mallory",
    email: "mallory@acme.test",
    name: "Mallory Quinn",
    orgRole: "guest",
    roleLabel: "Guest",
    departments: [],
  },
} as const satisfies Record<string, DemoUser>;

export type UserKey = keyof typeof USERS;

export const ALL_USERS: readonly DemoUser[] = Object.values(USERS);

/** Owners, admins and auditors see Administration (apps/web/src/lib/session.ts). */
export function seesAdministration(user: DemoUser): boolean {
  return user.orgRole === "owner" || user.orgRole === "admin" || user.orgRole === "auditor";
}

/** Everything test code creates starts with this, so a leftover is easy to find and remove. */
export const E2E_PREFIX = "E2E browser";
