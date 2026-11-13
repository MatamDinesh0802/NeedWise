# 0012. Document access lists that only narrow, in the core; permission sync in `ee/`

- Status: Accepted (implemented)
- Date: 2026-11-13
- Amends: [0006](0006-two-lock-permission-aware-retrieval.md) and the collection-level recheck
  of [0008](0008-retrieval-fusion-and-sensitivity-routing.md)

## Context
Access was per collection only. A synced Drive folder, Slack workspace or GitHub organization
became one collection, and everyone who could read the collection read every item in it, even a
file shared in Drive with two people. The threat model listed this as a residual risk ("no
per-document grants"), and the ROADMAP puts permission sync from the sources in the Enterprise
edition. Two things had to be decided: what a document-level grant means, and where each part
lives.

The OpenFGA model had a `document` type whose `viewer` was a union (`... or viewer from
collection`): a document grant could only widen access. The app never wrote such tuples.

## Decision
1. **A document's own list only narrows.** To see a document a principal must be able to see
   its collection **and** be on the document's list, if it has one. A list never opens a
   collection. In OpenFGA: `define viewer: viewer from collection and grantee`, where a document
   without a list has the wildcard grantee `user:*` and one with a list names exactly its
   principals, so an empty list means nobody. The widening relations are removed.
2. **The mechanism and its enforcement are in the core (Community).** `documents.own_acl` and
   `document_grants` in PostgreSQL are the source of truth. Lock 1 gets `own_acl` and `doc_acl`
   in every chunk's payload and a filter clause (no list, or a list naming the caller). Lock 2
   keeps checking every passage's collection (which never races a just-uploaded document's own
   tuples) and also checks `viewer` on the document for every passage whose document has a list
   according to PostgreSQL **or** the payload, so either signal suffices; for those documents
   lock 2 also applies the PostgreSQL list itself (one query), which closes the window in which
   both OpenFGA (outbox) and the payload (refresh after commit) still lag. Every non-retrieval
   path asks the same question in SQL (`document_acl.visible_clause`, `can_view`).
3. **Identity mapping fails closed.** Every principal a source names is kept as written
   (`email:`, `group:`, `domain:`, `anyone`, `slack-user:`, `github-*`). Only `email:` (the
   user with that address) and `anyone` (the organization) map on their own; everything else
   needs an admin mapping. An unmapped entry stays on the list without a principal: it grants
   nothing and is listed for admins. Mapping changes apply at once.
4. **Reading ACLs from sources is Enterprise** (`permission_sync`), through a core hook
   (`PERMISSION_SYNC`) that the sync engine calls. The engine reads ACLs before applying any
   content, writes existing documents' lists first, creates new documents with their list in
   the same transaction, fails the whole sync on an outage, and leaves an item to nobody when
   the source refuses to show its permissions. Drive revocations also come from the changes feed
   every minute. Without the license, lists already written stay; nothing new is read.
5. **Editors may set a list on uploaded documents** in Community, never on synced or meeting
   documents (their access follows the source), and never one that leaves themselves out.

## Consequences
- The residual risk "no per-document grants" is closed for Drive, Slack and GitHub (with the
  license) and for uploads (in both editions). Lists store groups and departments, so membership
  changes still need no re-index.
- A document with a list costs one more OpenFGA check per answer and one PostgreSQL query per
  search; documents without one cost the extra filter clause only.
- After an upgrade, `nw authz sync` (or the next seeding) writes the wildcard grantee of every
  existing document. Until then nothing changes: lock 2 checks documents only when they have a
  list, and points without `own_acl` count as having none.
- Without a license, lists written earlier go stale (they narrow, but removals in the source no
  longer arrive). This is documented rather than hidden by widening everything back.
- Admins who cannot read a document see that it needs attention, never its title; the audit log
  still names files, as for restricted collections.
