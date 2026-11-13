# Document access lists and permission sync

A document normally has the access of its **collection** ("this Drive folder → Finance"). A
document may also carry **its own access list**: the people, groups and departments who may
see it. The list only ever **narrows** the collection's access: to see the document you must
be able to see the collection **and** be on the list. A list never opens a collection to
anyone. The design is recorded in [ADR 0012](adr/0012-document-access-lists.md).

| | Community (Apache-2.0) | Enterprise (`permission_sync`) |
|---|---|---|
| Enforcing document lists everywhere (both retrieval locks and every other path) | Yes | Yes |
| An editor limits an uploaded document (`PUT /documents/{id}/acl`) | Yes | Yes |
| Identity mapping, the list of unmapped source principals | Yes | Yes |
| Lists read from the source during every sync (Drive, Slack, GitHub) | No: synced documents keep their collection's access | Yes |
| Drive revocations applied without waiting for the next sync | No | Yes (the changes feed, every minute) |

Security basics stay in Community: the mechanism, its enforcement and the tests are in the
core. What Enterprise adds is reading ACLs from the source systems (`ee/python/src/workmind_ee/permission_sync.py`).

## How it is enforced

The list is stored in PostgreSQL (`documents.own_acl`, table `document_grants`, both under
forced row-level security) and projected, like collection grants, into the two places
retrieval checks ([ARCHITECTURE §7.6](ARCHITECTURE.md#76-permission-aware-retrieval-two-locks)):

- **Lock 1, the Qdrant pre-filter.** Every chunk carries `own_acl` and `doc_acl` (the list's
  principals). A search matches only chunks whose collection ACL names one of your principals
  **and** that have no own list or whose `doc_acl` names one of your principals. Chunks indexed
  before lists existed have no `own_acl` field and count as having none, which is what they are.
- **Lock 2, the OpenFGA recheck.** In the model a document's `viewer` is
  `viewer from collection and grantee`. A document without a list has the wildcard grantee
  `user:*`; one with a list names exactly its principals, so an empty list leaves nobody. Before
  any model call, every passage's collection is checked as before, and every passage whose
  document has a list (according to PostgreSQL **or** the chunk's payload) is also checked as a
  document. A passage that fails is dropped and counted as drift.

Both are derived from the same rows: a change to a list writes the OpenFGA tuples to the
outbox in the same transaction and refreshes the chunks' payload right after the commit.
Ingestion reads the list again just before it stores the chunks, so a change made while a
document was being embedded is not lost. A test checks, for every Acme person, that the
PostgreSQL rule, the Qdrant filter and OpenFGA give the same answer
(`tests/integration/test_document_acl.py::test_both_locks_agree_for_every_person_on_every_document`).

Every other path that shows something about a document asks the same question with the
caller's principals:

| Path | A document whose list leaves you out |
|---|---|
| Knowledge Hub (`/documents/{id}`, `/chunks`, `/file`, `/acl`, every change) | 404, the same answer as for a document that does not exist, also for the collection's editors |
| Collection lists and counts (`/collections`, MCP `list_collections`) | Left out of the list and of the count |
| Search, chat, `/ask`, agents, research, MCP `ask` and `search_knowledge` | Never retrieved, never sent to a model, never cited |
| Governance overview, stale documents (admins) | Listed without its title or note |
| Official-status requests | You cannot request, decide or be chosen as an approver; requests are shown without title |
| Document owner | Cannot be made its owner; review reminders and demotion notices are not sent to an owner the list leaves out |
| `document.ready` workflows | No run for a workflow whose owner the list leaves out |

What is **not** filtered: the audit log (admins and auditors see events about every document,
including file names, as for restricted collections), the admin collection and source lists'
document counts, and the answers you received before you lost access (they stay in your own
conversations, as for collections).

## Identity mapping: fail closed

A source names people and groups its own way. Permission sync keeps every entry the source
lists, as `<kind>:<value>` in lower case, and maps it to someone in WorkMind:

| Source principal | Mapped without an admin |
|---|---|
| `email:<address>` (Drive people, Slack members whose e-mail Slack gives) | The WorkMind user with that e-mail |
| `anyone` (Drive "anyone with the link") | The organization (members, not guests), still within the collection |
| `group:<address or id>` (a Google group), `domain:<domain>` (Drive "anyone at acme.test") | No |
| `slack-user:<id>` (a member whose e-mail is hidden) | No |
| `github-user:<login>`, `github-team:<org>/<slug>`, `github-org:<org>` | No |

An entry that maps to nobody **grants nothing**: it stays on the list without a principal. A
list whose entries all map to nobody means nobody sees the document, not even the collection's
editors. Nothing is guessed from names.

Administrators see what is unmapped and map it:

```bash
# Unmapped source principals: how many documents name each, from which sources (no titles)
curl -H 'Cookie: ...' http://localhost:8480/api/v1/admin/identity-mappings
# Map a Google group to a WorkMind group: applied at once to every document that names it
curl -X PUT -H 'Cookie: ...' -H 'X-CSRF-Protection: 1' -H 'Content-Type: application/json' \
  http://localhost:8480/api/v1/admin/identity-mappings \
  -d '{"external": "group:eng-leads@acme.test", "principal": "group:<uuid>"}'
# Remove a mapping: the people it let in lose access at once
curl -X DELETE ... http://localhost:8480/api/v1/admin/identity-mappings/<mapping id>
```

A mapping names a WorkMind principal (`user:`, `group:`, `dept:`, `dept-manager:` or `org:`
with an id of this tenant). An explicit mapping wins over the e-mail match (for aliases). Saving
or deleting one changes the lists of every document that names that source principal in the
same transaction, queues the OpenFGA changes and refreshes the chunks. There is no screen for
this yet: the API above is the interface.

## Lists set by an editor (Community)

A collection editor who can see an **uploaded** document can limit it:

```bash
PUT /api/v1/documents/{id}/acl   {"viewers": ["user:<uuid>", "dept:<uuid>"]}
PUT /api/v1/documents/{id}/acl   {"viewers": null}        # back to the collection's access
GET /api/v1/documents/{id}/acl                              # the list, for its editors
```

The editor must stay on the list (`422 would_lose_access`), and every principal must belong to
the tenant. Synced documents follow their source and meeting documents their meeting
(`409 acl_from_source`). `DocumentOut.own_acl` tells the web app a document has its own list.

## Permission sync (Enterprise)

With the feature `permission_sync` licensed ([enterprise.md](enterprise.md)), every sync of a
Drive, Slack or GitHub source reads who may see each item and writes it as the document's own
list. The order inside a sync:

1. scan the source (as before);
2. read the ACLs of everything scanned. If the source is down or rate-limits, the sync fails
   and **nothing changes**; it is retried at the next interval;
3. write the lists of documents synced before, and refresh their chunks: a narrowed list
   applies before any new content is indexed;
4. new items are created with their list in the same transaction, so they are never indexed
   or visible with the collection's wider access, even for a moment;
5. changed content is re-indexed with the current list; deletions as before.

What each connector reads:

| Connector | Read | List |
|---|---|---|
| Google Drive | Each file's permissions (`files/{id}/permissions`, every page, shared drives included) | People (`email:`), groups (`group:`), domains (`domain:`), anyone with the link (`anyone`). Every role reads; deleted accounts are skipped |
| Slack | `conversations.info`; for a private channel `conversations.members` and `users.list` | A public channel keeps the collection's access (any workspace member can read it). A private channel's documents, every month of it including months the scan no longer lists, are limited to its members: `email:` when Slack gives the address (scope `users:read.email`), else `slack-user:<id>`. Bots and deleted accounts are left out. Needs `groups:read` for private channels |
| GitHub | The repository's visibility, `collaborators?affiliation=all` and `teams` | Public: the collection's access. Private: collaborators (`github-user:`) and teams (`github-team:`). Internal: also `github-org:`. Listing collaborators needs a token with push access |
| Web crawler | Nothing | Pages have no ACL: the collection's access |

**Fail closed per item.** When the source answers but refuses to show an item's permissions
(Drive "insufficient permissions", a private channel the bot left, collaborators the token may
not list), that item's list becomes empty, so nobody sees it until a later sync can read it, and
the source shows the error ("permissions not read, nobody sees it"). The sync's stats count
`acl_changed` and `acl_unmapped`.

### How fast a change upstream takes effect

| Change in the source | When WorkMind applies it |
|---|---|
| Someone removed from a Drive file, a file unshared, trashed or deleted | Within a minute: the task `workmind_ee.permission_sync.drive_changes` reads each Drive source's changes feed every 60 s and applies the current permissions of every changed file at once (a removed or trashed file: nobody, until the next sync deletes it). The first run only records where the feed starts. If Valkey loses the feed position, the next run starts again from now and the regular sync covers the gap |
| Any other Drive change; Slack membership; GitHub collaborators, teams, visibility | At the next sync of the source (`interval_minutes`, at least 5, default 60), or at once with **Sync now** |
| An admin maps or unmaps a source principal | At once |
| Someone joins or leaves a WorkMind group or department | At once: lists store groups and departments, not expanded members |

Lock 1 changes when the chunks are refreshed, right after the commit; lock 2 when the OpenFGA
outbox applies the change (the worker polls every 2 s). Until both have, the other one still
holds.

That the Drive changes feed reports sharing changes is how Google documents it; it was tested
here only against a fake of the API, not a real Drive (the Phase 7 DoD for a live Drive and
Slack is still open).

### Without the license

Without `permission_sync`, connectors keep the collection-level mapping only: a sync never asks
the source for ACLs and every new synced document gets its collection's access. Lists written
while the feature was licensed **stay as they were**: they still narrow, but changes in the
source no longer reach them (someone removed in Drive keeps the access the old list gave them),
and the Drive changes task stops. To go back to collection-level access for a source, delete
and re-create it, or license the feature again.

## Tests

- `apps/api/src/workmind/modules/authz/openfga/model.fga.yaml`: the authorization matrix (in
  the collection but not on the list, on the list but not in the collection, admins, a guest
  named on a list, a grant in a collection the person cannot read, an empty list).
- `tests/integration/test_document_acl.py` (Community): a canary in a limited document never
  reaches Bob, who is in the collection, through search in three modes, chat, `/ask`, research,
  agents, MCP or the Knowledge Hub, nor any model call made for him (the analytics endpoints,
  which never read documents, carry nothing of it either); the locks agree for every person;
  lock 2 holds when the chunks lose their list; titles are withheld in governance and stale
  lists; owners, approvers and workflow owners must be able to read the document; identity
  mappings apply and revoke at once.
- `tests/integration/test_permission_sync.py` (both editions): a fake Drive synced with and
  without the license, a person removed at the next sync, the changes feed revoking at once,
  refusals failing closed, an outage changing nothing, lists after the license lapses.
- `ee/python/tests/test_permission_sync.py`: what is read from Drive, Slack and GitHub.
- `tests/integration/test_document_acl_redteam.py` and `evals/datasets/red_team.jsonl`
  (`rt-60` to `rt-68`, kind `document-acl`): the seeded Acme corpus has two documents with
  their own lists (an embargoed security advisory in Engineering Docs, a reorganisation draft
  in the handbook); the attacks come from inside the collection, from the list but outside the
  collection, as a guest and as an HR member who is not a manager, through chat, agents and
  research, and run in `--retrieval-only` mode in CI.
- `tests/integration/test_cross_tenant.py` classifies the new routes.

## Not built

- A screen for identity mappings and for a document's list (the API exists).
- Push notifications from Drive (`changes.watch` needs a public HTTPS endpoint); the changes
  feed is polled every minute instead.
- Immediate revocation for Slack and GitHub (their events are not read: changes apply at the
  next sync), SharePoint and Confluence (no connectors yet).
- Mapping by Slack or GitHub profile without an admin (only e-mail is matched on its own).
- Per-section or per-message permissions inside a document: a Slack month is one document.
