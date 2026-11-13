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

- Only an approver of the request can decide. Nobody approves their own request for official
  status, and an approver rule never includes the person who asked.
- A double click, or two devices, never runs an action twice. When a request went to several
  people, the first decision counts and the others' copies say so.
- Some requests need more than one approval, in order (for example, your manager, then Finance):
  each step's card says *Step 1 of 2*, and the action runs only after the last step approves.
- A proposal that waits gets a reminder before it expires, and after a while (your
  organization sets how long) it also goes to someone else: the approver's backup, their
  manager, or the org admins. The card says *Escalated to you*.
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
  **Triage** (org admins): requests nobody could route with confidence, to send to a team.
- **New task**: assign it to yourself, a colleague or a team's queue, or to nobody yet, with an
  optional description and due date.
- On a task's page you can change its status (open, in progress, done, cancelled), claim it from
  a queue, or hand it over to a colleague. Whoever receives a task is notified.
- A task is visible to its assignee, its creator and the department whose queue it is in. Anyone
  else gets "Task not found".
- A task says where it came from, for example *From the assistant* or *From a meeting*, with a
  link back to the meeting.

The assistant creates tasks with its `create_task` tool, which goes through an approval like
any other change. Mention a colleague in a description with `@` and their address
(`@wei.zhang@acme.test`) to tell them, if they can see the task.

## Requests

**Requests** (`/requests`) is for when you don't know which team handles something: describe it,
optionally pick a category, and WorkMind sends it to a department's queue. Your organization's
rules decide first; otherwise a model may, if it is confident enough; otherwise it waits in
triage for a person. You see at once where it went and why, and it stays under **Created by me**.
A request that reached the wrong queue can be sent on by the people in that queue.

## My commitments

The **My commitments** section of Tasks lists the action items from meetings that name you as
the owner, with the due date, the task's status, and a link that opens the meeting at the moment
it was said ([Meetings](../meetings.md)).

## Notifications

The bell in the header shows what needs you: an approval waiting, a reminder or an escalation,
a decision on something you asked for, a proposal that expired or failed to run, a task
assigned to you or due soon, a request for your team, a mention, meeting notes that are ready,
documents you own that are due for review, and budget warnings.

In **Settings › Notifications** you choose, per kind, whether a copy also reaches you by email
(right away, or in a daily digest at the hour you pick) or as a Slack message (when your
admins connected Slack), and you name a **backup approver** who gets your approvals when they
wait too long.

## From code

`GET /api/v1/approvals`, `POST /api/v1/approvals/{id}/decision`, `GET` and `POST /api/v1/tasks`,
`PATCH /api/v1/tasks/{id}`, `POST /api/v1/requests`, `GET /api/v1/notifications` and
`GET`/`PUT /api/v1/me/work-preferences`: see
[Automation and workflows](../automation.md) and the [HTTP API reference](../reference/api.md).
