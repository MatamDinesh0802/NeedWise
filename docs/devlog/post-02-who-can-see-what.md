# LinkedIn post #2: Who is allowed to see what?

- **Status:** Draft, not posted
- **Milestone:** Phase 1 (identity, tenancy, permissions)
- **Image:** `assets/post-02-permission-grid.png` (1080×1350). Every cell in it is an assertion in `apps/api/src/workmind/modules/authz/openfga/model.fga.yaml`, so the picture can't drift from the code.
- **Optional second image:** the admin audit log with "Chain intact" (captured once the admin UI ships).
- **Post only after** the repo is public.

---

## Post

Most enterprise AI demos skip the hardest problem.

Not "can the AI answer?" but "should it answer *you*?"

This week I built the permission layer of WorkMind AI, an open-source AI workspace for companies. Before a single document is indexed, it now answers one question everywhere: who is allowed to see what.

What I built, and the rule behind each piece:

1. Company data is isolated by PostgreSQL itself, not just by my code.
Row-Level Security is forced on every company table. If a query forgets the company filter, it returns nothing instead of someone else's data. A test fails the build if any future table skips it.

2. Permissions are relationships, not just roles.
"Bob can read this because he is in Engineering, which can read that collection." That's OpenFGA, an open-source authorization system inspired by Google's Zanzibar. Nested groups, inheritance, and one store per company.

3. Admins are not gods.
An IT admin can manage users but cannot read salary files unless someone grants it. An auditor can read the audit log but not the company's documents.

4. Every promise is a test.
The grid in the image (6 people × 7 collections of a fictional company, Acme Robotics) is 42 automated checks, part of 83 that run on every change. "The engineer never sees salaries." "The contractor sees exactly one collection."

5. The audit log can't be quietly edited.
Each event is hash-chained to the previous one. Change one record and verification points to exactly where.

The lesson that surprised me: in PostgreSQL, foreign-key checks ignore Row-Level Security. A guessed ID from another company could still be linked. The fix: foreign keys that include the company ID. A test now proves the database refuses that link.

Next: documents. They will inherit exactly these permissions, checked twice: once while searching, once before answering.

How does your team decide what an internal AI assistant is allowed to show?

Repo link in the first comment.

#OpenSource #AIEngineering #Security #RAG #BuildInPublic

---

## First comment

Repo (Apache-2.0): <GITHUB_REPO_URL>
Permission model + the Acme test matrix: <GITHUB_REPO_URL>/tree/main/apps/api/src/workmind/modules/authz/openfga
Architecture, section 7 (permissions): <GITHUB_REPO_URL>/blob/main/docs/ARCHITECTURE.md
