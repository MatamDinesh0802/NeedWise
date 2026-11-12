# Knowledge governance and the audit explorer

Answers are only as good as the documents behind them. Knowledge governance makes three things
explicit for every document: **who answers for it** (its owner), **when it was last checked**
(its review cycle), and **whether answers may treat it as official** (an approval, not a
checkbox). The audit explorer is how an administrator or auditor reads what happened.

Everything on this page is in the Community edition. Two related features are Enterprise:
retention policies and the SIEM export of the audit chain ([enterprise.md](enterprise.md)).
Design background: [ARCHITECTURE 7.3](ARCHITECTURE.md#73-trust-levels-and-sensitivity-labels)
(trust levels).

Permissions are checked in code on every request (`api/v1/knowledge.py`,
`modules/knowledge/governance.py`), never by a model.

## Who can do what

| Action | Who | API |
|---|---|---|
| Change a document's owner | Collection editors; org owners and admins | `PUT /api/v1/documents/{id}/owner` |
| Mark a document reviewed | The document's owner; collection editors | `POST /api/v1/documents/{id}/review` |
| Ask for official status | Collection editors | `POST /api/v1/documents/{id}/official` |
| Approve or reject official status | The collection's owner; org owners and admins when the owner is the one asking or there is none. Never the person who asked. | `POST /api/v1/approvals/{id}/decision` |
| Remove official status | Collection editors (immediate) | `PATCH /api/v1/documents/{id}` |
| Set a collection's owner and review interval | Org owners and admins | `PATCH /api/v1/admin/collections/{id}` |
| See the governance overview | Org owners and admins: every collection. Anyone else: the collections they own. | `GET /api/v1/governance/overview` |
| Read, filter and export the audit log | Org owners, admins and auditors | `GET /api/v1/admin/audit-events`, `.../export.csv` |

## Owners

Every document has an owner: the person who answers for its content and is reminded to review
it. A new document is owned by the person who uploaded it; when there is no uploader (a
connector without one), by the collection's owner. Migration `0018` gave existing documents the
same default.

Collection editors and org admins hand a document to someone else from the document page
(**Change owner**, which searches the colleague directory `GET /api/v1/people`). The new owner
must be an active person who can open the collection: an owner who cannot read the document
cannot review it, so the API refuses with `422 owner_cannot_view`. An admin may change owners
in a collection they cannot read (separation of duties); the answer then holds no content. The
new owner gets a notification, and the change is audited (`knowledge.document.owner_changed`).

A collection also has an owner (set in **Admin > Collections**). It approves official status for
the collection's documents and sees the collection in its governance overview. Like an editor,
the collection's owner can view and edit the collection; changing it updates both permission
locks (OpenFGA and the search index) like a sharing change does.

## Review cycles

A collection may set `review_interval_days` (1 to 3650). Each document is then due for review
that many days after its last review, or after it was added: `next_review_at`. Without an
interval nothing is ever due. Changing the interval reschedules every document of the collection
from its last review.

**Reminders.** The Celery beat task `workmind.send_review_reminders` runs once a day. For each
document past `next_review_at`, it creates **once per review cycle**:

- a task for the owner, "Review <title>", due in 14 days, linking to the document;
- a notification for the owner;
- an audit event, `knowledge.document.review_reminded`.

The cycle's task is remembered on the document (`review_task_id`), and the rows are locked while
the task runs, so running it twice, or twice at the same time, reminds once. A document without
an active owner is not reminded; it is listed in the governance overview instead.

**Marking it reviewed.** The owner or a collection editor chooses **Mark reviewed** on the
document page (an optional note says what was checked). That sets `reviewed_at` and
`reviewed_by`, schedules the next review one interval from now, closes the cycle's task, and is
audited (`knowledge.document.reviewed`, with the note).

**In answers.** Review dates live in PostgreSQL, not in the search index, so a review never needs
a re-index. When an answer cites an official document whose review date has passed, its citation
and source carry `review_overdue: true`, and the Sources panel shows **Review overdue** next to
the trust badge. The document is still used and still ranked as official; the label tells the
reader that nobody has confirmed it recently. Answers stored before this existed have no flag
and show nothing.

## Official status

`official` is the highest trust level: answers rank official documents first and prefer them when
sources disagree. So it is reached only through an approval.

1. A collection editor chooses **Request official status** and says why (a required note).
2. The request goes to the collection's owner. When the owner is the one asking, or the
   collection has no active owner, it goes to every active org owner and admin except the
   person asking. Nobody ever approves their own request. With no possible approver the API
   answers `409 no_approver`.
3. The approver sees it in **Approvals** with what deciding means: approving makes the document
   official (and counts as a review); rejecting keeps its trust level. The arguments of this kind
   of approval cannot be edited (`422 not_editable`), and no agent or workflow can propose one:
   its name, `knowledge.official_status`, is not a tool name.
4. **Approve:** the API checks again, at that moment, that the approver is still the collection's
   owner or an org admin and is not the requester. Then the trust becomes `official`, the
   search index is updated at once, `reviewed_at`/`reviewed_by` are set, and the next review is
   scheduled. Audited as `approval.approved` and `knowledge.document.official_approved`; the
   requester is notified.
5. **Reject:** the trust stays as it was. Audited as `approval.rejected`; the requester is
   notified with the approver's note.

When a request went to several admins, the first decision answers it; the other copies become
`superseded` ("Decided by another approver"). A request expires after 14 days. While one is
pending, the document page shows **Official status requested** with who asked, who decides and
why, and a second request is refused (`409 official_pending`).

**No other path sets official.** `PATCH /api/v1/documents/{id}` with `trust: "official"` answers
`403 official_requires_approval`. The one standing exception is the collection's
`default_trust`: a new document takes it, whether it is seeded, synced by a connector or
uploaded. An administrator sets it when creating the collection, as a decision for everything
that lands there; the seeded Legal & Contracts collection uses it. Changing an existing
document to official always goes through the approval.

**Demotion is immediate.** A collection editor chooses **Remove official status**, picks
`reference` or `discussion` and may add a note. No approval is needed to lower trust. It is
audited (`knowledge.document.demoted`, with the note), the search index follows at once, and
the document's owner is notified. Making it official again needs a new approval.

## The governance overview

`GET /api/v1/governance/overview`, shown in **Admin > Governance** for org owners and admins
and at **Knowledge Hub > Governance** (`/knowledge/governance`) for collection owners. Org owners
and admins see every collection; anyone else sees the collections they own, and an empty
overview if they own none. It lists:

- **Overdue reviews:** `next_review_at` has passed.
- **Due within 30 days.**
- **Without an owner:** no owner, or an owner who has left (disabled).
- **Waiting for official approval:** pending requests, with who asked, who decides and why.
- **No longer official (30 days):** demotions, with who did it and their note.

Each list holds at most 200 documents. An admin who cannot open a collection sees that its
documents need attention, never their titles (separation of duties, ARCHITECTURE 7.1); the web
app says "A document in <collection> you can't open" instead of a link.

## The audit explorer

The audit log is append-only and hash-chained (`audit_events`; **Verify integrity** checks the
chain). **Admin > Audit log** reads it.

- **Filters** (`GET /api/v1/admin/audit-events`): `action` (a prefix, such as `knowledge.`),
  `actor_id`, `resource_type`, `resource_id`, `from` and `to` (ISO 8601 with a time zone; `from`
  inclusive, `to` exclusive; the web form's end day is inclusive), and `q`, free text matched in
  the action, the resource type and id, and the details. Every filter narrows the result. The
  web app keeps the filters in the page URL, so a filtered view can be shared or linked.
- **Paging:** newest first, `limit` up to 100, and `cursor`: pass the previous page's
  `next_cursor` to get the next, older page (`null` at the beginning of the log).
  `before_seq` still works for older clients.
- **CSV export:** `GET /api/v1/admin/audit-events/export.csv` takes the same filters and returns
  the newest 10,000 matching events at most (`X-Export-Truncated: true` when there were more;
  narrow the filters for older ones). Columns: `seq, occurred_at, actor_type, actor_id,
  actor_name, action, resource_type, resource_id, ip, details` (details as JSON). A cell that a
  spreadsheet would run as a formula (starting with `=`, `+`, `-`, `@`, a tab or a carriage
  return) is prefixed with `'`. Every export is itself audited as `audit.export`, with the
  filters and the number of events.
- Org owners, admins and auditors may read and export the log; anyone else gets `403`.

The complete chain with its hashes, as JSON lines for a SIEM, is the Enterprise feature
`advanced_audit_export` ([enterprise.md](enterprise.md#audit-export-for-a-siem)).

### Governance events in the log

| Action | When |
|---|---|
| `knowledge.document.owner_changed` | A document got another owner |
| `knowledge.document.review_reminded` | The daily task reminded an owner (actor: system) |
| `knowledge.document.reviewed` | Someone marked a document reviewed |
| `knowledge.document.official_requested` | An editor asked for official status |
| `approval.approved`, `approval.rejected` | The approver decided |
| `knowledge.document.official_approved` | The document became official |
| `knowledge.document.demoted` | An editor removed official status |
| `admin.collection.governance_changed` | An admin changed a collection's owner or review interval |
| `audit.export` | Someone exported the audit log (CSV or JSON lines) |

## The demo data

`make seed` gives each department collection an owner (someone who already edits it, so owning
it grants nobody new access), a review interval, and a fixed `last_reviewed` date from
`seed/acme/company.yaml`. The dates never come from the clock, so every machine shows the same
thing. As of November 2026:

| Collection | Owner | Interval | Last reviewed | Next review |
|---|---|---|---|---|
| Company Handbook | Alice Moreau | 365 days | 2025-11-03 | 2026-11-03: overdue |
| HR Guidance (internal) | Alice Moreau | 180 days | 2026-04-20 | 2026-10-17: overdue |
| Engineering Docs | Marcus Bell | 180 days | 2026-06-01 | 2026-11-28: due soon |
| Finance Reports | Hannah Schmidt | 90 days | 2026-09-01 | 2026-11-30: due soon |
| Operations SOPs | Olivia Grant | 365 days | 2025-12-01 | 2026-12-01: due soon |
| Support Knowledge Base | Samuel Mensah | 180 days | 2026-08-03 | 2027-01-30 |
| Legal & Contracts | Priya Raman | 365 days | 2026-03-02 | 2027-03-02 |

Seeding is idempotent: it only touches what the product has not changed (an owner still equal
to the uploader, a document never reviewed in the product), so running it again changes nothing
and never undoes a review or an owner chosen in the app.

## What is not built

- No escalation: an overdue review stays with its owner; nobody else is told when the owner
  ignores the task.
- No review history beyond the audit log: the document keeps only its latest review.
- Overdue reviews do not lower a document's ranking; they are only labelled.
- No bulk actions (hand over every document of a person who leaves, mark many reviewed).
- The overview is a list, not a report: no trends over time.

## Tests

`tests/integration/test_governance.py` (owners and defaults, reminders once per cycle and
idempotent, reviews closing the task, interval changes, official requests: approvers, no
self-approval, editors cannot set official directly, rejection, demotion, overview scoping,
`review_overdue` in sources, the seeded dates), `tests/integration/test_audit_explorer.py`
(filters, cursor paging, CSV of the filtered view, tenant isolation, admins and auditors only),
and the web tests next to the components (`*.test.ts(x)` under `apps/web/src`).
