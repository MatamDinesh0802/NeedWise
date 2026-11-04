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
- Every step is in the audit log: `approval.requested`, `approval.approved` (with `edited`),
  the tool call, `email.sent`, `approval.rejected`, `approval.expired`.

API: `GET /api/v1/approvals`, `GET /api/v1/approvals/{id}`,
`POST /api/v1/approvals/{id}/decision {decision, arguments?, note?}`.

## In chat: research mode

Send a message with `"mode": "research"` and the research agent may use tools. The run streams
`tool.step` events, plus `approval.required {approval_id, tool, summary}` for each proposed
action, which the chat shows as an inline card.

> *"Prepare this week's operations report and email it to leadership@acme.test."*
> → four permission-aware searches → a drafted email → an approval card → Olivia adds her
> sign-off and approves → the email is sent → all of it audited.

## Tasks

Tasks belong to a person, or to a department queue when nobody is assigned yet.
`GET /api/v1/tasks?scope=mine|queues|created`, `POST /api/v1/tasks`,
`PATCH /api/v1/tasks/{id}` (status, claim). The assistant creates them with the `create_task`
tool, which needs approval like every change.

## Workflows

Declarative, saved by an admin (`PUT /api/v1/admin/workflows/{key}`). A run acts as the admin who
saved it, with their current access.

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

## Email

`send_email` uses SMTP (`SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_USERNAME`, `SMTP_PASSWORD`,
`SMTP_STARTTLS`). In development every message lands in **Mailpit**, http://localhost:8025, and
nothing is delivered.
