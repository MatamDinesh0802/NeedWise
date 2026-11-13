# LinkedIn post #8: Connecting to Google Drive is easy. Keeping permissions in sync is the hard part.

- **Status:** Draft, not posted
- **Milestone:** Phase 7 (connectors and sync)
- **Visual (suggested, not made yet):** a diagram of one Drive file on its way in: its sharing list → identity mapping (an e-mail becomes a person; a Google group, a domain or a Slack account becomes nobody until an admin maps it) → the document's own list, which can only narrow its collection's → both permission locks. Beside it, the three side paths the final review found (a legal hold label, an escalation notice, an upload with the same file name). HTML source in `docs/devlog/assets/`, 1080×1350, like the earlier diagrams.
- **Numbers from:** `evals/results/2026-11-13-redteam-document-lists.md`, `evals/results/2026-11-13-v1.0.0-core.md` (the 9 `document-acl` attacks), `docs/permission-sync.md`, `docs/connectors.md`, and the final review's fixes in `CHANGELOG.md`.
- **Post only after** the repo is public.
- **Before posting:** if a live Google Drive and Slack workspace have been synced by then (the Phase 7 DoD), say so and drop the "not live yet" paragraph. Until then, keep it.

---

## Post

Connecting an AI assistant to Google Drive takes an afternoon.

Keeping it from showing people files they were removed from yesterday takes the rest of the project.

What I built for NeedWise:
• Six connectors: Google Drive, Slack, GitHub, Confluence, SharePoint/OneDrive and a web crawler, on one sync engine. A file is only deleted after a complete scan says it's gone, or the source reports the deletion itself
• Every document can carry its own access list. It can only narrow what its collection allows, never widen it, and both permission checks enforce it
• Identity mapping that fails closed. An e-mail address matches a person. A Google group, a whole domain or a Slack account grants nothing until an admin maps it. Unmapped means no access, not "probably fine"
• With the Enterprise edition, Drive revocations arrive within a minute (the changes feed is read every 60 s); Slack and GitHub changes at the next sync

The measured part: 9 attacks on documents with their own list. Someone in the collection but not on the list. Someone on the list but not in the collection. A guest. An HR member who isn't a manager. Through chat, department agents and the research agent.

0 leaks in the 27 retrieval checks the test suite runs on every change, and 0 leaks, 0 violations when Claude answered all 9, twice.

What didn't work: search was locked down from the start. The holes were on the side paths. A final review found that a legal hold label, an overdue-review notice and an "official status removed" message named a restricted document to people its list leaves out. And an upload with the same file name could replace a document the uploader couldn't even see. All fixed, each with a regression test.

And the honest gap: none of this has met a live Drive or Slack workspace yet. Everything is tested against fakes built from their API docs (the web crawler and GitHub ran against live sites). That check is still open.

When someone loses access to a file, how fast should your AI assistant forget it?

Repo, the permission design and the attack reports in the first comment.

#RAG #AISecurity #EnterpriseAI #OpenSource #BuildInPublic

---

## First comment

Repo (Apache-2.0): https://github.com/MatamDinesh0802/NeedWise
Document access lists and permission sync: https://github.com/MatamDinesh0802/NeedWise/blob/main/docs/permission-sync.md
Why lists only narrow (ADR 0012): https://github.com/MatamDinesh0802/NeedWise/blob/main/docs/adr/0012-document-access-lists.md
The 9 attacks, answered by a model: https://github.com/MatamDinesh0802/NeedWise/blob/main/evals/results/2026-11-13-redteam-document-lists.md
Connectors and what is not verified live: https://github.com/MatamDinesh0802/NeedWise/blob/main/docs/connectors.md
