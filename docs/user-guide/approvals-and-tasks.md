# Approvals and tasks

WorkMind can prepare work, but a person stays in control. Anything that changes something,
such as sending an email, creating a task or making a document official, becomes an
**approval**: a stored proposal that the right person approves, edits and approves, or rejects.
Nothing runs before that. This page is the user's view; how approvals are stored and how
workflows use them is in [Automation and workflows](../automation.md).

## Where approvals come from

| Source | Example |
|---|---|
| The assistant in **Research & act** mode | "Prepare this week's operations report and email it to leadership@acme.test" |
| A workflow an admin set up | A weekly report that waits for its approver before it is sent |
| The Knowledge Hub | An editor asks for a document to become official |
| A tool from a remote MCP server (`MCP_SERVERS`) | An ERP update that is not marked read-only |

The proposal says in plain words what will happen, for example *Send an email to
leadership@acme.test: Weekly Operations Report*. In a conversation it appears as a card under
the answer that proposed it.

## Decide

Open **Approvals** (`/approvals`), the card in the conversation, or the notification. You can:

- **Approve**: the action runs **once**, as the person who asked, with the access they have now
  (not the access they had when they asked).
- **Edit & approve**: change the proposal first, for example the email's text or recipients.
  The edited version is checked again before it runs, and the record shows it was edited.
- **Reject**: nothing runs. A workflow waiting for it is cancelled. You can add a note; the
  person who asked sees it.

Rules that always hold:

- Only the named approver can decide. Nobody approves their own request for official status.
- A double click, or two devices, never runs an action twice.
- A proposal nobody decides within 3 days expires, and nothing runs. The person who asked is
  told.
- If the person who asked has been disabled in the meantime, their action does not run.
- Every request and decision is recorded in the audit log.

The **Approvals** page lists what waits for you, and keeps the approvals you decided and the ones
you asked for, with their outcome.

## Tasks

**Tasks** (`/tasks`) holds your work from people, the assistant, workflows and meetings:

- **Mine**: tasks assigned to you. **My team queues**: tasks waiting in your departments'
  queues, which anyone in the department can claim. **Created by me**: tasks you created.
- **New task**: assign it to yourself, a colleague or a team's queue, or to nobody yet, with an
  optional description and due date.
- On a task's page you can change its status (open, in progress, done, cancelled), claim it from
  a queue, or hand it over to a colleague. Whoever receives a task is notified.
- A task is visible to its assignee, its creator and the department whose queue it is in. Anyone
  else gets "Task not found".
- A task says where it came from, for example *From the assistant* or *From a meeting*, with a
  link back to the meeting.

The assistant creates tasks with its `create_task` tool, which goes through an approval like
any other change.

## My commitments

The **My commitments** section of Tasks lists the action items from meetings that name you as
the owner, with the due date, the task's status, and a link that opens the meeting at the moment
it was said ([Meetings](../meetings.md)).

## Notifications

The bell in the header shows what needs you: an approval waiting, a decision on something you
asked for, a proposal that expired or failed to run, a task assigned to you, meeting notes that
are ready, documents you own that are due for review, and budget warnings.

## From code

`GET /api/v1/approvals`, `POST /api/v1/approvals/{id}/decision`, `GET` and `POST /api/v1/tasks`,
`PATCH /api/v1/tasks/{id}` and `GET /api/v1/notifications`: see
[Automation and workflows](../automation.md) and the [HTTP API reference](../reference/api.md).
