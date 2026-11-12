# Usage, costs and budgets

WorkMind records every model call it makes for an organization, prices it, and lets owners and
admins set budgets that warn or stop model use. This page says what is recorded and what is
not, how each call is attributed, who sees which usage, how budgets are enforced (and by how
much they can be overshot), what is Enterprise, and how older usage is imported.

Code: `apps/api/src/workmind/modules/usage/`, the API in `api/v1/usage.py`, the Enterprise
parts in `ee/python/src/workmind_ee/` (`budgets.py`, `chargeback.py`). Screens: the Usage page
(`/analytics`), Admin › Budgets, and "Your usage this month" on the Overview.

## What is recorded

**One usage event per model call** that goes through the organization's model resolution
([docs/models.md](models.md)): a completion, a streamed answer (recorded when the stream ends),
a structured output, and a tool loop (one event for the whole loop, with the tokens of every
turn). This is the one place every model call passes (`TenantLLM` in
`modules/model_catalog/resolver.py`), so no feature records usage on its own.

Each event (table `usage_events`) holds:

| Field | Meaning |
|---|---|
| `occurred_at` | When the call ended (database time, UTC) |
| `user_id` | The person the call worked for; empty for evaluations |
| `department_id` | The department the usage counts for (see "Attribution") |
| `agent_key` | The department agent that made the call, if any |
| `feature` | `chat`, `agents`, `research`, `workflow`, `meetings`, `analytics`, `agent_test`, `eval` (`other`: see below) |
| `model`, `model_id`, `provider`, `local` | The model name the provider reported, its catalog id, the provider kind (`anthropic`, `openai_compatible`), whether requests stay inside your infrastructure |
| `input_tokens`, `output_tokens` | As the provider reported them |
| `cost_usd` | The price of those tokens; empty when the model has no price |
| `ref_type`, `ref_id` | The record the call belongs to: `message` (a chat answer), `analytics_run`, `meeting`, `workflow_run` |
| `latency_ms` | How long the call took |
| `status` | `ok`, `failed` or `cancelled` |
| `source` | `live`, or `backfill` for imported history |

**Never the text.** No prompt, answer, document or tool result is stored with usage.

**Failed and stopped calls count too**, with what the provider had reported when they ended:

- A tool loop that fails on its third turn is recorded with the tokens of the first two.
- A structured reply that does not validate was still produced and billed: it is recorded with
  its tokens (status `failed`). A refusal from Claude is recorded with its tokens too.
- A streamed answer stopped by the person (or by a server shutdown) is recorded as `cancelled`.
  Claude reports input tokens when the stream starts and output tokens as it goes, so the event
  has them; OpenAI-compatible servers report usage only at the end of a stream, so a stopped
  stream from them is recorded with 0 tokens.
- A call that fails before the provider reports anything (connection refused, HTTP 5xx) is
  recorded with 0 tokens, so the number of requests stays true.
- A call refused by a budget is **not** recorded: no model was called.

**Cost** is computed per response from the price of the model that served it: the
organization's price in its model catalog (Admin › Models) first, else the table in
`providers/pricing.py` (Claude Sonnet 5.5 at $2 / $10 and Claude Opus 5.5 at $4 / $20 per
million input / output tokens), else none. A call with no price (local models, unknown names)
has an empty `cost_usd`, is left out of cost totals, and is counted as an *unpriced request*.
Cost is never estimated.

**Recording never fails the request.** The event is written after the call; if writing fails,
the event is lost, the API logs `usage_event_dropped` (with the error type and the count so far)
and counts it in the process (`UsageMeter.dropped`). It costs one insert per model call.

### What is not recorded

- Embeddings (indexing documents, searching) and transcription (meeting audio).
- The provider test in Admin › Models (one small request to check a key).
- `wm seed` corpus generation (it belongs to no organization).
- Model calls that do not go through the organization's model resolution. None are known; if
  one happens anyway it is recorded with feature `other` and no person, and the API logs
  `usage_unattributed`.

## Attribution

Every entry point says what its calls are for, before it starts (a context variable that the
tasks it starts inherit, `modules/usage/calls.py`):

| Feature | Where | Person | Record |
|---|---|---|---|
| `chat` | Assistant answers (`POST /conversations/{id}/messages`, `ASSISTANT_MODE=single`), `POST /api/v1/ask`, the MCP `ask` tool | the asker | `message`: the stored answer (conversations only) |
| `agents` | The same, with `ASSISTANT_MODE=agents` or `auto`: planning, each department agent, composing | the asker | `message` |
| `research` | "Research & act" in chat, `POST /api/v1/research` | the asker | `message` (chat only) |
| `workflow` | Research steps of workflow runs, whatever started them (schedule, a person, `document.ready`) | the workflow's owner | `workflow_run` |
| `meetings` | Extracting notes from a meeting | the person who uploaded it | `meeting` |
| `analytics` | `POST /analytics/ask` (Insights) | the asker | `analytics_run` |
| `agent_test` | The test console in Admin › Agents | the administrator | — |
| `eval` | `wm eval` | nobody | — |

A department agent's calls carry its key (`agent_key`); in the test console every call carries
the tested agent's key.

**Department.** A person can belong to several departments; their usage counts for one: the
department they joined first (their oldest membership; on a tie, the smaller department id).
It is decided when each event is recorded, so moving someone changes where their later usage
counts, never the history. People in no department, and evaluations, have none.

## Who sees which usage

`GET /api/v1/usage/summary` answers at the caller's scope:

| Caller | Scope | Sees |
|---|---|---|
| Owner or admin (browser session) | `tenant` | everyone's usage, every budget and every person's cap |
| Department manager (browser session) | `departments` | the usage of the people in the departments they manage (all of their usage), the organization's budgets, the budgets of their departments, their own cap |
| Anyone else, and every access token | `self` | their own usage, the organization's budgets, their departments' budgets, their own cap |

Grouped by person or department, a `self` summary shows only the caller. `GET /usage/events`
(every recorded call, newest first, with filters) and the chargeback report are for owners and
admins only. Days are UTC calendar days.

## Budgets

A budget is a limit in US dollars for a **UTC day** or a **calendar month in UTC**, for:

- the organization (`tenant`);
- one department (`department`, Enterprise, see below): the usage that counts for it;
- one person (`user`): their cap. The **default cap** applies to everyone who has no cap of
  their own; a person with their own cap is limited by it alone.

Each budget either **blocks** (model requests are refused once it is used up) or only **warns**.
Every budget warns once per period when `warn_at` (default 80%) of it is spent. Periods reset
at 00:00 UTC (day) and on the first of the month at 00:00 UTC; each status carries `resets_at`.

Owners and admins set them in Admin › Budgets (`GET` / `PUT /api/v1/admin/budgets`; `PUT`
replaces every budget and the default cap; a budget keeps its id while its scope, subject and
period stay the same). Changes are audited (`budgets.updated`).

### Enforcement

A call is limited by the organization's budgets, the caller's cap (or the default cap), and the
budget of the department their usage counts for. When a blocking one is used up:

- Chat, agents and research (`POST /conversations/{id}/messages`, `/api/v1/ask`,
  `/api/v1/research`), Insights (`POST /analytics/ask`) and the agent test console answer
  **429** before doing anything (the question is not stored):

  ```json
  {
    "type": "about:blank",
    "title": "Budget exceeded",
    "status": 429,
    "code": "budget_exceeded",
    "detail": "The monthly organization budget of $500.00 is used up ($512.40 spent). Requests that use an AI model are paused until it resets at 2026-12-01 00:00 UTC.",
    "budget": {"id": "…", "scope": "tenant", "subject": null, "period": "month", "limit_usd": 500.0, "spent_usd": 512.4, "warn_at": 0.8, "status": "exceeded", "resets_at": "2026-12-01T00:00:00Z"}
  }
  ```

- The same test runs before **every model call**, so a chat answer that crosses the limit half
  way ends with its usual `run.failed` event carrying `code: "budget_exceeded"`, the same detail
  (as `message` and `detail`) and the `budget`. The partial answer is stored as failed.
- The MCP `ask` tool returns a tool error with the same detail, also before doing anything.
- Meeting extraction fails the meeting with that detail as its error; a workflow run fails its
  research step and ends `failed` with it (the owner is notified as for any failed run). Neither
  answers 429: they run in the background.
- When several budgets are used up, the one that resets last is reported.
- Evaluations (`wm eval`) are recorded but never blocked.
- Calls to local models are refused like any other while a blocking budget is used up, although
  they add nothing to it.

**Warnings** use the in-app notifications (kind `budget.warning`, or `budget.exceeded` when the
first event past the warning level also used the budget up), linking to the Usage page:
organization budget → owners and admins; department budget → the department's managers, owners
and admins; a person's cap → that person. Each is sent once per budget, person (for caps) and
period (table `budget_alerts`).

### How spending is counted, and the overshoot bound

Spent-so-far is cached in Valkey per organization, scope, subject, period and period start. A
missing value is summed from `usage_events` and kept for 60 seconds; each recorded event adds
its cost to the values that exist. The budgets themselves are cached in each API process for 10
seconds (the process that saves them drops its copy at once).

Cost is known only after a call, so **a budget can be overshot**:

- by the calls let in while it was still below its limit: each request in flight can finish
  the model call it is making. With Claude Sonnet 5.5 and 16,000 output tokens (the largest
  limit a call asks for), one call costs at most $0.16 plus its input tokens. Department agents
  run in parallel (up to 4 per question), so a question in agents mode can have 4 calls in
  flight;
- by events recorded in the moment between a cached value being summed from the database and
  that value being stored (they are counted at the next refresh, within 60 seconds);
- by usage written straight to the table (the backfill) until the cached values refresh;
- for 10 seconds after a budget is changed, in API processes other than the one that saved it;
- with department budgets, for 30 seconds after someone moves to another department: each API
  process keeps which department budget limits a person for that long (recording is not
  affected: each event takes the department at the moment it is written).

A call that starts after the spending has reached the limit is always refused.

## Enterprise

Two parts are WorkMind Enterprise ([docs/enterprise.md](enterprise.md)); the core stores every
budget and records every event, and `workmind-ee` plugs in through two hooks
(`usage.department_budgets`, `usage.chargeback_report`, declared in
`modules/usage/extensions.py`):

- **Department budgets** (`department_budgets`): a department budget limits the people whose
  usage counts for that department; owners and admins see every department's budget, everyone
  else those of the departments they belong to. Without the feature, saving one answers **402**
  `enterprise_feature` and nothing is stored. If a license expires, department budgets stop
  being shown and enforced at that moment; the rows are kept (they apply again with a license)
  and saving budgets leaves them untouched.
- **The chargeback report** (`chargeback_reports`): `GET /api/v1/usage/export.csv?from&to&group_by=department`
  (owners and admins), a CSV with one row per department that used a model in the range, largest
  cost first, then `No department`:

  ```text
  department,department_id,people,requests,input_tokens,output_tokens,cost_usd,unpriced_requests
  People & HR,0190…,2,2,200,20,1.500000,0
  No department,,1,2,200,20,0.250000,1
  ```

  Names that start with `=`, `+`, `-`, `@`, a tab or a carriage return are written with a
  leading `'` so a spreadsheet shows them instead of running them. Each export is audited
  (`usage.exported`). Without the feature: **402** `enterprise_feature`.

Organization budgets, personal caps, the default cap, warnings and every report other than the
chargeback CSV are Community.

## Importing older usage

```bash
cd apps/api && uv run wm usage backfill            # every active organization
cd apps/api && uv run wm usage backfill acme       # one, by slug
docker compose exec api wm usage backfill          # the same, inside the Docker Compose stack
```

On the demo stack (after `make seed` and some use), the Compose command printed
`acme: imported 21 answers, 10 BI answers, 2 meetings`.

Before usage events existed, usage was kept with some records. The backfill imports:

- chat answers (`messages.usage`): one event each, `research` when the answer has tool steps,
  else `chat` (agents-mode answers cannot be told apart and count as `chat`);
- Insights answers (`analytics_runs.answer.usage`): one event each, `analytics`;
- processed meetings (`meetings.timings.model`): only the model's name was kept, so each is one
  request with 0 tokens and no cost.

Workflow runs and the test console kept no usage, so nothing is imported for them.

**Never twice.** A record that already has a live event (same `ref_type` and `ref_id`) is
skipped: it was recorded live. Imported events are unique per record (index
`uq_usage_events_backfill`) and the insert skips duplicates, so running the command again adds
nothing. One answer becomes one event, so imported request counts are per answer, not per model
call. The department is the person's department when the command runs; the provider is
`unknown` and there is no latency.

## Tables and permissions

Migration `0017`: `usage_events`, `budgets`, `budget_alerts`, all with forced Row-Level Security
(`tenant_isolation`). The runtime role may insert usage events but not update or delete them
(`REVOKE UPDATE, DELETE, TRUNCATE`), like the audit log. Deleting a person or a department keeps
their usage, without the link. Retention policies (Enterprise, [enterprise.md](enterprise.md#retention-policies))
delete conversations, Insights runs and recordings but never usage events: an event keeps its
`ref_type` and `ref_id` (no foreign key), and its record may no longer exist.

## Tests

With scripted models (`tests/fakes.py`, no real provider): `tests/test_usage_unit.py` (periods,
costs, attribution, one event per call of each kind, partial usage of failed and stopped calls,
refused calls), `tests/integration/test_usage_features.py` (one event per call for chat, agents
mode, research, the test console, workflows and meetings, with person, department, agent and
record; a chat run stopped half way by a budget), `test_usage_analytics.py` (Insights),
`test_usage_budgets.py` (saving and validating budgets, 429 on every entry point and the MCP
`ask` tool error, caps, period boundaries, warnings once, append-only, tenant isolation, the
backfill, who sees what, the event list) and `test_usage_editions.py` (department budgets and the chargeback CSV with the real
`workmind-ee` loaded; nothing enforced once the license expires).

Checked on the running stack (2026-11-12, `ASSISTANT_MODE=auto`): one real assistant answer with
Claude Sonnet 5.5 was recorded as one live event (`agents`, the asker, their department, the
message ref) with the tokens Anthropic reported (2,773 in, 301 out) and $0.008556, which is those
tokens at $2 / $10 per million. The usage summary, the event list, the chargeback CSV and
saving budgets (with a department budget) were called live as an admin, a department manager and
an employee, and their responses match the web app's types.

Not measured: the time recording adds to a model call against a loaded database; a stopped or
failed call against a real provider (those paths are tested with scripted models only); an
OpenAI-compatible or tenant-key provider.
