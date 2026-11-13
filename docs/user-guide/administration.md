# People, departments, groups and roles

The first three pages of **Admin** decide who is in the organization and how they are grouped.
Collections, sources and agents share knowledge with these departments and groups, so everything
else builds on them. This page covers **Admin › People**, **Admin › Departments** and
**Admin › Groups**, the organization roles, and the rules that keep an organization from locking
itself out.

Every change is checked by the API (`/api/v1/admin/...`), never by the page: hiding a menu is a
convenience. Each change is written to the audit log (**Admin › Audit log**) in the same
transaction, and the permission service (OpenFGA) is updated from an outbox in that transaction
too, so a change takes effect at the next request. Roles and memberships are read from
PostgreSQL on every request: someone who loses a role loses its access at once.

## Organization roles

Each person has one organization role.

| Role | Can |
|---|---|
| **Owner** | Everything an admin can, and grant or remove the owner role. At least one active owner is kept (below). |
| **Admin** | Every Admin page except granting the owner role: people, departments, groups, collections' access, sources, agents, workflows, automation, models, budgets, retention and legal holds. Managing access does not let them read restricted documents: they read what their own departments, groups and grants allow. |
| **Auditor** | The audit log (read, filter, export, verify the chain); the list of legal holds through the API (`GET /api/v1/admin/legal-holds`, Enterprise). Nothing else in Admin. Auditors read audit logs, not knowledge: like guests, they see only what is shared with them, their groups or their departments directly. |
| **Member** | The product: assistant, Knowledge Hub, meetings, Insights, tasks and approvals, within their access. |
| **Guest** | Like a member, but only what is shared with them (or a group or department they are in) directly: what is shared with "everyone in the organization" leaves guests out. Guests are never approvers. |

Owners of a collection (any role) also manage that collection's documents and sources from the
Knowledge Hub ([connectors](../connectors.md)); that is collection ownership, not a role.

## Adding people

**Admin › People › Add person** takes an email, a name, an optional title and a role. The person
appears with the status **Invited**.

What it does and does not do today:

- It creates the WorkMind user only. When a person signs in through Keycloak for the first time
  with that **verified** email, the sign-in links to this user and the status becomes **Active**.
  An email that is not verified never links (otherwise anyone able to set an unverified address
  in an identity provider could take over the account).
- **No invitation email is sent, and no Keycloak user is created.** Someone has to make the
  login exist:
  - With an identity provider connected to Keycloak (Okta, Entra ID, Google), the person signs
    in with it; nothing else is needed if the provider sends a verified email.
  - Without one, an operator creates the user in Keycloak's admin console
    (`/auth`, realm `workmind`): the same email, marked verified, a temporary password or the
    *Update Password* required action, and membership of the organization whose alias is the
    tenant's slug (for the demo, `acme`). `make seed` does this for the demo people.
- Only an owner can add an owner (`403 owner_required`); an email already in the organization
  answers `409 email_taken`.

## Changing a role, disabling an account

From a person's **Actions** menu: **Change role** and **Disable…** (or **Enable…**).

- **Disabling** ends the person's sessions at once, stops their access tokens, removes them from
  approver lists and escalations, and keeps everything they made. Their memberships stay, so
  enabling them again restores their access as it was. Someone who never signed in goes back to
  **Invited** when enabled.
- Guard rails, enforced by the API:
  - **Nobody changes their own role or status** (`403 self_change`): another admin does it.
  - **Only an owner grants or removes the owner role**, and only an owner disables or enables an
    owner (`403 owner_required`).
  - **The last active owner stays**: demoting or disabling them answers
    `409 last_owner` ("The organization needs at least one active owner").

## Departments

**Admin › Departments** lists the departments with their managers and member counts.
**New department** takes a name and a slug (the key that rules, collections and agents refer
to, for example `finance`). A department page adds people as **member** or **manager**, changes
that role, and removes them; **Rename department** changes the name, not the slug; **Delete**
removes the department and its memberships.

What the manager role does elsewhere:

- Approver rules can name "a manager of the requester's department" or "a manager of finance"
  ([automation](../automation.md)), and escalations go to the approver's managers.
- Request routing puts requests in the department's queue, which its members work and its
  managers are told about.
- A collection can be shared with a department's members (`dept:`) or only its managers
  (`dept-manager:`).

A person can be in several departments. The seeded Acme company has one manager per department.

## Groups

**Admin › Groups** holds groups of people for sharing (for example `leadership` or
`finance-leads`). **New group** takes a name, a slug and a description. A group page adds people
(**Add member**) and other groups (**Add nested group**): a nested group's members count as
members of the outer group, at any depth. A nesting that would make two groups contain each
other is refused (`409 group_cycle`).

Groups with a **Source** other than local come from an identity provider or a connector and are
changed there; WorkMind refuses to edit them (`409 managed_externally`).

Where groups are used: collection and document access (`group:`), approver `any_of` lists are
people rather than groups, and identity mappings can map a source's group to a WorkMind group
([permission sync](../permission-sync.md)).

## Where to look next

- Who can see each collection: **Admin › Collections** and [the Knowledge Hub guide](knowledge.md).
- What changed and who changed it: **Admin › Audit log** (`admin.user.created`,
  `admin.user.updated`, `admin.department.*`, `admin.group.*`).
- Several organizations on one deployment: [multi-tenant mode](../multi-tenancy.md).
