# Automation: approvals, tasks and workflows

WorkMind does work, with people in control. **Anything that changes something** (sending an
email, creating a task, a remote tool marked external) becomes an *approval*: a stored proposal
that a person approves (possibly after editing it) or rejects. Nothing waits in memory, so a
decision can come days later, from another device, after restarts (ADR 0009).

## Approvals

| When | What happens |
|---|---|
| The assistant (research mode) or a workflow wants to act | The action is validated and stored with a plain-words summary; the approvers are notified |
| Approve, optionally with edits | The stored action (or the edited version, validated again) runs **once**, as the person who asked, with the access they have *now*. In a chain, the next step's approvers decide first |
| Reject | Nothing runs; a waiting workflow is cancelled |
| Nobody decides for a while | A reminder before it expires, and an escalation to someone else after the SLA ([below](#reminders-and-escalation)) |
| No decision before `expires_at` (3 days) | It expires; nothing runs |

- Only an approver of the request can decide it.
- A double click or two devices can never run an action twice: the decision claims the
  approval atomically. A request with several approvers (a rule, an escalation) has one copy
  per approver; the first decision answers for all and the others become `superseded`, with a
  link to where it was decided.
- If the requester has been disabled in the meantime, their action does not run.
- Every decision is in the audit log: `approval.requested` (with the approvers and the rule),
  `approval.approved` (with `edited`, and the step of a chain), `approval.forwarded`, the tool
  call, `email.sent`, `approval.rejected` (recorded as `approval.rejectd` before 2026-11-12; the
  log is append-only, so older rejections keep that name), `approval.reminded`,
  `approval.escalated`. An approval that expires is not audited: the requester gets an
  `approval.expired` notification.

API: `GET /api/v1/approvals?status=pending|all` (yours as approver or requester, newest 100;
narrow it with `conversation_id=` or `workflow_run_id=`), `GET /api/v1/approvals/{id}` (with
`stage`, `stages` and the `rule` in words), `POST /api/v1/approvals/{id}/decision {decision,
arguments?, note?}`.

### Who approves: approver rules

By default the person who asked confirms their own assistant's action, and a workflow's owner
approves its steps (or the person its step names). A **rule** sends the request to others:

```yaml
approver: olivia.grant@acme.test                    # one named person (as before)
approver: {role: org_admins}                        # any org admin
approver: {department_manager: requester}           # a manager of the requester's department
approver: {department_manager: finance}             # a manager of the Finance department
approver: {any_of: [hannah.schmidt@acme.test, {role: org_admins}]}   # any one of them
approver:                                           # all of these steps, in order
  chain:
    - {department_manager: requester}
    - {any_of: [hannah.schmidt@acme.test, priya.raman@acme.test]}
```

- **Each step is an any-of group**: everyone it names gets a copy and the first decision
  counts. A **chain** (at most 5 steps) moves to the next step only when the step approves:
  that copy becomes `forwarded`, the next step's approvers get copies with the arguments *as
  approved so far* (an edit carries over), and the action runs only when the last step
  approves. A rejection at any step ends it.
- **Never self-approval:** a rule never includes the person who asked (a single named person
  keeps its old meaning, so a workflow's owner may still name themselves). Guests never
  approve; disabled people are left out.
- People are resolved when their step starts, with the directory as it is then. A request
  whose rule leaves a step with nobody is refused at once (`no_approver`: the research agent
  says so; a workflow step fails); a chain whose later step has nobody left when it starts
  fails instead of running half-approved.
- **Where rules apply:** a workflow step's own `approver`; otherwise the organization's rule for
  that tool (Admin › Automation), for workflow steps and for the actions the assistant proposes
  in research mode; otherwise the default above.
- Saving a workflow or the rules checks that every person and department named exists, and
  that each step can be met by somebody other than the admin saving it (`422
  invalid_approver`). Edits at decision time are validated as before (`422 invalid_input`).

### Reminders and escalation

A beat task (`workmind.escalate_work`, hourly) applies the organization's SLAs, set in
Admin › Automation (`PUT /api/v1/admin/automation`, `escalation`):

| Setting | Default | What happens |
|---|---|---|
| `remind_before_hours` | 12 | The approver is reminded this long before the approval expires |
| `escalate_after_hours` | 24 | Still pending this long after it was asked: a copy goes to the escalation target |
| `escalate_to` | `backup` | Where to start: the approver's **backup approver** (each person sets theirs in Settings), else their department's **managers**, else the **org admins** (`manager` and `org_admins` start further down) |
| `task_remind_before_hours` | 24 | An assigned task's assignee is reminded this long before it is due |
| `task_escalate_after_hours` | 24 | This long past its due date, the assignee's escalation target is told (only people who can open the task; else the queue's managers, else whoever created it) |
| `queue_escalate_after_hours` | 48 | A queue task nobody claimed for this long: the queue's managers are told (a request waiting in triage: the org admins) |

- **Once:** each step stamps the approval or task (`reminded_at`, `escalated_at`) in the same
  transaction as its notifications, so it happens once per approval (each step of a chain has
  its own copies, so once per step) and once per task, however often the task runs; rows are
  locked with `SKIP LOCKED` so overlapping runs never both act. 0 turns a step off; `enabled:
  false` turns all of them off.
- An escalated copy keeps the original's deadline: **expiry stays the last step**. The original
  approver can still decide; the first decision counts. The original approver is told.
- **Not escalated** (still reminded): an approval waiting for the person who asked (they confirm
  their own assistant's action: there is nobody to escalate past), an escalated copy itself, and
  Knowledge Hub requests for official status (they already go to the collection's owner or the
  org admins, docs/governance.md).
- Audited: `approval.reminded`, `approval.escalated` (who, and via backup, manager or admins),
  `approval.escalation_skipped` (nobody to escalate to), `task.reminded`, `task.escalated`,
  `task.escalation_skipped`.

## Notifications by email and Slack

Notifications always appear in the app. Each person chooses, per category, whether a copy also
goes out (Settings › Notifications, `GET/PUT /api/v1/me/work-preferences`):

| Category | Notifications |
|---|---|
| `approvals` | approvals waiting for you, reminders, decisions on what you asked for |
| `assignments` | tasks given to you, due-date reminders, requests for your queue or for triage, where your request went |
| `escalations` | approvals and tasks escalated to you or past you; official documents whose review is overdue (docs/governance.md) |
| `reviews` | documents you own that are due for review, ownership changes, demotions; synced documents that look stale (docs/connectors.md) |
| `sources` | a source you manage keeps failing to sync, or syncs again (docs/connectors.md) |
| `legal_holds` | a legal hold was placed or released (Enterprise, for owners, admins and auditors; docs/enterprise.md#legal-hold) |
| `budgets` | budget warnings and budgets used up |
| `mentions` | a colleague mentioned you in a task |
| `other` | failed workflow runs, meeting notes that are ready |

- **Email:** off (the default), right away, or in a **daily digest** at the hour (UTC) the
  person picks: their unread notifications of those categories since the last digest, at most
  a day back. Emails go through the SMTP adapter (`SMTP_*`; Mailpit in development) as plain
  text: the title, the body, and a link to the page (`PUBLIC_URL` + the notification's link),
  with a footer pointing back to the settings.
- **Slack direct message:** when an admin has saved a **bot token** (Admin › Automation; a Slack
  app with `users:read.email` and `chat:write`). Slack finds the person by their email address
  (`users.lookupByEmail`, then `chat.postMessage`).
- **Slack channel:** an admin may also save an **incoming webhook** and choose categories to post
  to that channel. A channel gets one post per thing (its kind and link), however many people
  were told, with the title and a link: everyone in the channel reads those titles, so choose
  categories whose titles they may see.
- **Never blocking:** `notify()` writes the notification and its outbox rows
  (`notification_deliveries`) in the action's own transaction; the beat task
  (`workmind.deliver_notifications`, every minute) queues digests and sends what is due. A
  failure is logged and retried after 1, 5, 25 and 120 minutes, then marked failed; a wrong
  token, a person Slack doesn't know, or missing SMTP is not retried. The action and its in-app
  notification are never affected. Sent and failed rows are pruned after 30 days.
- **Secrets:** the bot token and webhook URL are sealed with the deployment's key ring
  (`SECRETS_ENCRYPTION_KEY`, AES-256-GCM, bound to the tenant and purpose), never returned by the
  API, never written to the audit log, and re-encrypted by `wm secrets rotate`. Webhook URLs must
  start with `SLACK_WEBHOOK_PREFIX` (default `https://hooks.slack.com/`); `SLACK_API_URL` points
  the Web API at a stub for tests. "Send me a test message" and "Post a test message" check them.

API (admins): `GET/PUT /api/v1/admin/slack {bot_token?, webhook_url?, channel_categories}`
(leave a secret out to keep it, `null` removes it), `POST /api/v1/admin/slack/test {target:
bot|webhook}`.

**Mentions:** `@wei.zhang@acme.test` in a task's description (the Request form, a new task, the
assistant's `create_task`) notifies that colleague, if they can open the task; the author and the
assignee are not told twice.

**Backup approver:** in the same settings, an active colleague (not yourself, never a guest) who
gets your approvals when they wait too long. Changing it is audited (`me.work_preferences.updated`).

## Tasks

Tasks belong to a person, or to a department queue when nobody is assigned yet, or wait in
**triage** when a request could not be routed with confidence ([below](#request-routing)).
`GET /api/v1/tasks?scope=mine|queues|created|triage`, `POST /api/v1/tasks`,
`GET /api/v1/tasks/{id}` (assignee, creator, the queue's department, or, for a request in
triage, a triager; others get a 404), `PATCH /api/v1/tasks/{id}` (status, claim, hand over) and
`POST /api/v1/tasks/{id}/route {department, note?}`. Whoever a task is assigned or handed to gets
a `task.assigned` notification, unless they assigned it to themselves. The assistant creates them
with the `create_task` tool, which needs approval like every change. In the app, `/tasks/{id}`
shows one task (with its routing decision), and members pick assignees from the colleague
directory (`GET /api/v1/people?q=`).

## Request routing

A **request** is a task for whichever team handles it. Three ways in:

- the **Request form** (`/requests`, `POST /api/v1/requests {title, description?, category?,
  due?}`; `GET /api/v1/requests/options` lists the categories), for members;
- **email** (a stub): a mail bridge holding an org admin's access token posts each message to
  `POST /api/v1/admin/requests/inbound-email {from, subject, body}`; the sender must be an active
  member (else `422 unknown_sender`, audited as `request.email_refused`) and the request is
  theirs. Nothing in WorkMind reads a mailbox;
- the assistant's **`create_task`** with neither an assignee nor a department (after approval).

Each is routed:

1. **Rules first** (Admin › Automation, `GET/PUT /api/v1/admin/routing-rules`): per department,
   keywords (whole words or phrases in the title and details, case ignored) and categories (the
   form's choices; a category outweighs any number of keywords). The department with the most
   matches gets it; a tie goes on to the model.
2. **The router model second** (role `router`, the tenant's model): it sees the departments, their
   rules' terms, and the request inside `<request>` as data; it answers a department and its
   confidence. It counts only at the **threshold** or above (default 0.7; `routing.threshold`),
   and only for a department that exists. Its calls count in usage as feature `routing`, for the
   person who asked.
3. **Otherwise triage:** the request waits with no queue. Org admins (and the members of a triage
   department, if one is set: `routing.triage_department`) see it under Tasks › Triage, are
   notified (`request.triage`), and route it by hand. Members of a queue can also send an
   unclaimed request on to another queue.

Every decision is stored on the task (`routing`: the method, department, confidence, matched
terms and the **explanation** in words, e.g. *"Matched the Finance rule: “invoice”, “refund”."*
or *"No rule matched. The model was not sure enough (People & HR, confidence 0.42; it needs
0.70): …"*) and audited as `request.routed` (the reason, not the request's text). The queue's
managers are told (`request.queued`); a request by email or from the assistant tells its sender
where it went (`request.routed`). `POST /api/v1/admin/routing/preview` shows where a request would
go without creating anything. The Acme seed has rules for seven departments
(`seed/acme/company.yaml`, seeded only into a tenant without rules).

What a crafted request can do: reach the wrong queue, where a person sees it. The model's
answer can only name one of the tenant's departments or none, and routing decides a queue, never
who may see what (queue members see the task as for any task in their queue).

**Evaluation:** `evals/datasets/request_routing.jsonl` (39 requests: 23 the seeded rules must
route alone, 11 only the model can, 5 too vague for anyone but a person, two with injected
instructions). `uv run wm eval --skip-retrieval --suites request_routing` runs them through the
same decision with the tenant's rules, threshold and model and reports the correct queue, wrong
departments (the costly error) and triage counts; with `--retrieval-only` it checks the rules
alone. The unit tests check the rules part on every run. A failed router call is counted as a
model error in the report, never as a cautious triage.

Measured on 2026-11-13 (commit `07fe2ba`, the Acme seed's rules, router model
`claude-sonnet-5-5` at the default threshold 0.7, one run;
[`2026-11-13-request-routing`](../evals/results/2026-11-13-request-routing.md)):
36/39 to the correct queue (rule items 23/23, model items 8/11, person-only items 5/5), **1 to a
wrong department** (a finance request the model sent to management at confidence 0.70), 2 model
items to triage at 0.55 and 0.60, 0 failed model calls. Before that commit every router call
failed: the schema sent to Claude carried `minimum`/`maximum`, which the API refuses, so the
model step never ran. One run of 39 items is a smoke test, not a quality claim.

## Workflows

Declarative, saved by an admin (`PUT /api/v1/admin/workflows/{key}`). A run acts as the admin who
saved it, with their current access. Triggers: manual only (no `trigger`), a cron `schedule`, or
an `event` (below). The definition is written in **YAML** (or JSON, which is YAML too):

```yaml
# Admin › Workflows keeps the comments.
description: Every Monday, last week's operations, emailed to leadership once approved.
trigger:
  schedule: "0 8 * * MON"        # cron, UTC; leave the trigger out for manual-only workflows
steps:
  - id: gather
    research: Summarize this week's operations: safety, shipping lead times, returns, incidents.
  - id: send
    tool: send_email
    arguments:
      to: [leadership@acme.test]
      subject: "Weekly operations report {{ today }}"
      body: "{{ steps.gather.text }}"
    approver:                     # default: the workflow's owner (or the tenant's rule for the tool)
      chain:
        - {department_manager: requester}
        - olivia.grant@acme.test
```

- **YAML or an object:** `PUT` takes `source` (the text, kept with its comments and returned as
  `source`) or `definition` (an object, shown back as YAML). Problems come back as `422
  invalid_workflow` with `errors: [{line, column, path, message}]`; `POST
  /api/v1/admin/workflows/validate {source}` checks a text without saving it (the editor calls it
  as you type). YAML is read with the safe loader, at most 100 KB, without aliases (`*name`), and
  dates become ISO strings.
- **Research steps only read**: company search and read-only tools.
- **Tool steps** run directly if they only read. If they change something, they are validated,
  then become an approval (with the step's approver rule), and the run waits (`waiting_approval`)
  until the last decision resumes it.
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
- **In the app:** Admin › Workflows edits the definition in a line-numbered editor (each problem
  marked on its line, with a button that puts the cursor there), runs a workflow now, shows each
  run's steps and outputs, and deletes workflows. Notifications about a run open
  `/admin/workflows?workflow={key}&run={id}`, with that run expanded.

### Event triggers

```yaml
trigger: {event: document.ready, collection: company-handbook}   # the filter is optional
```

| Event | When | Filter | Runs only if the owner can open |
|---|---|---|---|
| `document.ready` | a document version finished indexing (an upload, a new version, a synced item, a meeting transcript) | `collection` | the document's collection |
| `document.updated` | the same, for a version after the first: the content changed | `collection` | the document's collection |
| `meeting.processed` | a meeting's notes are ready | `collection` | the meeting's collection |
| `task.completed` | a task is marked done | `department` (its queue) | the task (as assignee, creator, queue member or triager) |
| `approval.decided` | an approval's decision is final: executed, failed or rejected (not a chain's forwarded step, not a superseded copy, not an expiry) | `tool` | the approval (as its approver or requester) |

- **When:** the event's own transaction (ingestion marking the version ready, the meeting
  pipeline marking it ready, the task update, the decision) queues one run per enabled workflow
  whose trigger matches. Whoever caused the event then starts the runs (the Celery worker sends
  one `workmind.start_workflow_run` task per run; the API process starts them in the
  background); the `beat` service starts any run still queued after a minute.
- **Exactly once per event:** each run carries a key unique per workflow:
  `document.ready:<version id>`, `document.updated:<version id>`, `meeting.processed:<meeting
  id>`, `task.completed:<task id>` (a task reopened and completed again does not trigger again),
  `approval.decided:<approval id>`. A repeated or re-delivered event finds the run already there.
  Starting a queued run claims it atomically, so the worker and the beat sweep never both run it.
- **As the owner, with their current access:** if the owner cannot open what the event is about,
  no run is created and the event tells them nothing. A workflow's own approvals never trigger it
  again (no loops). Saving a workflow whose filter names a collection the admin cannot view, or a
  department that doesn't exist, is refused (`422 trigger_collection`, `422 trigger_department`).
- **The event** is available as `{{ event.<key> }}`, always with `name` and `link`:
  documents: `document_id`, `version_id`, `version`, `title`, `filename`, `source`, `url`,
  `collection`, `collection_name`; meetings: `meeting_id`, `title`, `summary`, `decisions`,
  `action_items`, `collection`, `collection_name`; tasks: `task_id`, `title`, `description`,
  `assignee`, `department`, `completed_by`; approvals: `approval_id`, `tool`, `decision`,
  `status`, `summary`, `note`, `requested_by`, `decided_by`. In a research step they are quoted as
  `<data>` (someone else wrote them).
- **Audited:** `workflow.triggered` (workflow key, run, event, and what it is about), then the
  run's own `workflow.succeeded` / `failed` / `cancelled` and every step as for other runs.
- Runs from events show `An event` as their trigger in Admin › Workflows › Runs; a run waiting to
  start shows as *Queued*.

## Email

Guests (external contractors) cannot use `send_email` or `create_task`: research mode does not
offer them, and a call that reaches them anyway is refused. A guest's `POST /api/v1/tasks` can
only create a task for themselves, and `PATCH /api/v1/tasks/{id}` cannot hand one to anyone else;
guests cannot send requests to queues. In a research step, values from `{{ event.* }}` and
`{{ steps.* }}` are inserted as `<data>` elements (someone else wrote them: a document's author,
or a model that read documents); in tool arguments they are inserted as they are.

`send_email` and notification emails use SMTP (`SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`,
`SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_STARTTLS`). In development every message lands in
**Mailpit**, http://localhost:8025, and nothing is delivered.

## What was checked, and what is not built

Checked by tests (unit and integration against PostgreSQL and Valkey, with a scripted model, a
stub mailer and a stub Slack): rules and chains (no self-approval, edits carried to the next
step, a chain that loses its approvers fails), reminders and escalations happening once, the
outbox with retries and digests, routing by rules, by the scripted model and to triage over HTTP,
the email stub, YAML errors at their lines, each event trigger exactly once and only for what the
owner can open, and every new route in the cross-tenant isolation suite. The new pages (Settings ›
Notifications, Requests, Tasks › Triage, a request's page, Admin › Automation, the workflow
editor) were rendered in headless Chromium with the API mocked in the browser: no console errors
and no serious or critical axe findings. They have not been walked through against a running,
seeded stack.

Not built:

- Microsoft Teams notifications; Slack's interactive buttons (approving from Slack). Slack
  messages are plain text with a link.
- Reading a mailbox (IMAP or a provider's inbound webhook) for email requests: only the stub
  endpoint for a bridge.
- Per-workflow or per-step SLAs (the escalation policy is per organization), working hours or
  holidays in SLAs, and reassigning a task automatically when it escalates (people are told; a
  person reassigns).
- A digest more or less often than daily; HTML email.
- Event filters other than the one per event (e.g. by sensitivity or file type), and events for
  inbound email.
- A larger request routing eval: one run of 39 requests is measured (see Request routing), with
  no repeat runs, no other model and no threshold sweep.
