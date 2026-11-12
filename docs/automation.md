# Automation: approvals, tasks and workflows

WorkMind does work, with people in control. **Anything that changes something** (sending an
email, creating a task, a remote tool marked external) becomes an *approval*: a stored proposal
that a person approves (possibly after editing it) or rejects. Nothing waits in memory, so a
decision can come days later, from another device, after restarts (ADR 0009).

## Approvals

| When | What happens |
|---|---|
| The assistant (research mode) or a workflow wants to act | The action is validated and stored with a plain-words summary; the approver is notified |
| Approve, optionally with edits | The stored action (or the edited version, validated again) runs **once**, as the person who asked, with the access they have *now* |
| Reject | Nothing runs; a waiting workflow is cancelled |
| No decision before `expires_at` (3 days) | It expires; nothing runs |

- Only the named approver can decide.
- A double click or two devices can never run an action twice: the decision claims the
  approval atomically.
- If the requester has been disabled in the meantime, their action does not run.
- Every decision is in the audit log: `approval.requested`, `approval.approved` (with
  `edited`), the tool call, `email.sent`, `approval.rejected` (recorded as `approval.rejectd`
  before 2026-11-12; the log is append-only, so older rejections keep that name). An approval
  that expires is not audited: the requester gets an `approval.expired` notification.

API: `GET /api/v1/approvals?status=pending|all` (yours as approver or requester, newest 100;
narrow it with `conversation_id=` or `workflow_run_id=`), `GET /api/v1/approvals/{id}`,
`POST /api/v1/approvals/{id}/decision {decision, arguments?, note?}`.

## In chat: research mode

Send a message with `"mode": "research"` and the research agent may use tools. The run streams
`tool.step` events, plus `approval.required {approval_id, tool, summary}` for each proposed
action, which the chat shows as an inline card.

The answer is stored with its steps (`tool_steps` on the message: each tool's name, whether it
worked, the error code, and the approval it proposed; not the tools' inputs or results). After a
reload the chat shows the same list of steps, and fetches the conversation's approvals
(`GET /api/v1/approvals?status=all&conversation_id=…`) to put each card, pending or decided,
back under the answer that proposed it.

> *"Prepare this week's operations report and email it to leadership@acme.test."*
> → four permission-aware searches → a drafted email → an approval card → Olivia adds her
> sign-off and approves → the email is sent → all of it audited.

## Tasks

Tasks belong to a person, or to a department queue when nobody is assigned yet.
`GET /api/v1/tasks?scope=mine|queues|created`, `POST /api/v1/tasks`, `GET /api/v1/tasks/{id}`
(assignee, creator or the queue's department; others get a 404), and `PATCH /api/v1/tasks/{id}`
(status, claim, hand over). Whoever a task is assigned or handed to gets a `task.assigned`
notification, unless they assigned it to themselves. The assistant creates them with the
`create_task` tool, which needs approval like every change. In the app, `/tasks/{id}` shows one
task, and members pick assignees from the colleague directory (`GET /api/v1/people?q=`).

## Workflows

Declarative, saved by an admin (`PUT /api/v1/admin/workflows/{key}`). A run acts as the admin who
saved it, with their current access. Triggers: manual only (no `trigger`), a cron `schedule`, or
an `event` (below).

```yaml
name: Weekly operations report
definition:
  trigger: { schedule: "0 8 * * MON" }        # cron, UTC; omit for manual-only workflows
  steps:
    - id: gather
      research: Summarize this week's operations: safety, shipping lead times, returns, incidents.
    - id: send
      tool: send_email
      arguments:
        to: [leadership@acme.test]
        subject: "Weekly operations report {{ today }}"
        body: "{{ steps.gather.text }}"
      approver: olivia.grant@acme.test        # default: the workflow's owner
```

- **Research steps only read**: company search and read-only tools.
- **Tool steps** run directly if they only read. If they change something, they are validated,
  then become an approval, and the run waits (`waiting_approval`) until the decision resumes it.
- **Templates** fill `{{ steps.<id>.text }}`, `{{ steps.<id>.data.<key> }}`, `{{ today }}` and
  `{{ event.<key> }}`. A value that is exactly one placeholder keeps its type, e.g. a list of
  recipients.
- **Schedules** are checked every minute by the `beat` service. Run one beat, however many
  workers you have. Run now: `POST /api/v1/admin/workflows/{key}/run`.
  History: `GET /api/v1/admin/workflows/{key}/runs`.
- **Delete:** `DELETE /api/v1/admin/workflows/{key}` removes the workflow and its run history
  (audited as `admin.workflow.deleted`). It is refused (`409 runs_in_progress`) while a run is
  queued, running or waiting for an approval, because that approval could still run its action;
  disable the workflow to stop new runs.
- **In the app:** Admin › Workflows edits the definition (as JSON), runs a workflow now, shows
  each run's steps and outputs, and deletes workflows. Notifications about a run open
  `/admin/workflows?workflow={key}&run={id}`, with that run expanded.

### Event triggers: `document.ready`

A workflow can start when a document version finishes indexing (an upload, a new version, a
synced item, a meeting transcript):

```yaml
trigger: { event: document.ready, collection: company-handbook }   # collection: optional
steps:
  - id: summary
    research: "Summarize {{ event.title }} for the HR team."
```

- **When:** ingestion marks the version ready and, in the same transaction, queues one run per
  enabled workflow whose trigger matches (the event, and the collection key if one is given).
  Whoever ingested the document then starts the runs: the Celery worker sends one
  `workmind.start_workflow_run` task per run, the API process (inline ingestion) starts them in
  the background. The `beat` service starts any run still queued after a minute (e.g. the worker
  stopped right after the commit).
- **Exactly once per document version:** each run carries the key `document.ready:<version id>`,
  unique per workflow. A re-delivered ingestion task or a re-index of the same version finds the
  run already there; uploading identical bytes creates no version at all. Starting a queued run
  claims it atomically, so the worker and the beat sweep never both run it.
- **As the owner, with their current access:** the workflow's owner must be able to view the
  document's collection (the same rule as the Knowledge Hub and search). If they can't, no run
  is created and the event tells them nothing. Saving a workflow whose trigger names a
  collection the admin cannot view is refused (`422 trigger_collection`).
- **The event** is available as `{{ event.<key> }}`: `name` (`document.ready`), `document_id`,
  `version_id`, `version` (1, 2, ...), `title`, `filename`, `source` (`upload`, `meeting`, a
  connector), `url` (the original, for synced items), `link` (`/knowledge/documents/{id}`),
  `collection` (its key) and `collection_name`.
- **Audited:** `workflow.triggered` (workflow key, run, document, version, collection), then the
  run's own `workflow.succeeded` / `failed` / `cancelled` and every step as for other runs.
- Runs from events show `event` as their trigger in Admin › Workflows › Runs; a run waiting to
  start shows as *Queued*.

Not built: other events (meeting processed, inbound email), and filters other than the
collection (e.g. by sensitivity or file type).

## Email

Guests (external contractors) cannot use `send_email` or `create_task`: research mode does not
offer them, and a call that reaches them anyway is refused. A guest's `POST /api/v1/tasks` can
only create a task for themselves. In a research step, values from `{{ event.* }}` and
`{{ steps.* }}` are inserted as `<data>` elements (someone else wrote them: a document's author,
or a model that read documents); in tool arguments they are inserted as they are.

`send_email` uses SMTP (`SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_USERNAME`, `SMTP_PASSWORD`,
`SMTP_STARTTLS`). In development every message lands in **Mailpit**, http://localhost:8025, and
nothing is delivered.
