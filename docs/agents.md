# Agents as configuration

A department agent is a row of data, not code (Phase 11). An administrator adds one in
**Admin › Agents**, says what it is for, which collections it searches, which read-only tools it
may call, which model it uses and its limits, tries it in a test console on unsaved changes,
and saves it. It answers on the next question, in every process, with no deploy. Every save is
a numbered version that can be compared and restored.
Design: [ARCHITECTURE section 12](ARCHITECTURE.md#12-agent-orchestration).

Built (in `apps/api/src/needwise/modules/agents/`, `api/v1/agents.py`, migration `0013`, and
`apps/web/src/components/admin/agent-*.tsx`): everything on this page. The tests use a
scripted model (`tests/fakes.py`). **Measured** on 2026-11-12 with Claude Sonnet 5.5 planning:
with the Legal agent created as described [below](#create-the-legal-agent-step-by-step), first
through the API and then in the browser (Admin › Agents, no code), the routing evaluation picks
it for 15 of 15 legal questions (14 with exactly the expected agents), never picks it for a
question that does not need it, and gets 53 of 56 questions exactly right, the same numbers
both times ([results](#measured-routing-with-the-legal-agent)).
See [Not built and not measured](#not-built-and-not-measured).

## Where agents run

With `ASSISTANT_MODE=agents` (or `auto`, which uses agents only for questions that need several
areas), every chat question goes through the planner: answer directly, hand the question to one
agent, or split it into self-contained subtasks for several agents that run in parallel. Each
agent searches its collections, may call its tools, and returns findings with source markers;
one composer writes the cited answer. With `single` (the default), agents are not used.

**Agents never add access.** An agent searches *as the person asking*: both permission locks
apply (ARCHITECTURE 7.6), and the agent's collections only narrow what that person may read. Its
tools run with that person's scopes and permissions. Permissions are enforced in code; the model
only chooses what to search for.

## The fields

| Field | What it does |
|---|---|
| **Key** | A slug (`legal`), unique in the organization. The planner and the eval refer to agents by key. It can't be changed. |
| **Name** | Shown in chat ("Legal agent is searching…") and in the admin list. |
| **Description** | Read by the planner to decide who gets a question, and part of the fast path's profile. Write it for routing: what the agent answers, and what it is *not* for. Up to 1,000 characters. |
| **Instructions** | Optional guidance added to the agent's prompt (how to read and report its sources). Up to 2,000 characters. |
| **Collections** | The collections it searches. At least one. They narrow the asker's access; they never widen it. |
| **Example questions** | Up to 10. With the description and name they form the agent's *profile*, which the fast path compares with each question. |
| **Tools** | Read-level tools from the tool registry it may call besides searching ([Tools](#tools)). |
| **Model** | A model catalog id, or *Default for its role* ([Model](#model)). |
| **Limits** | Searches per question (1–5, default 2), tool calls per question (0–5, default 0; 2 when the first tool is chosen), output tokens per model call (1,000–16,000, default 6,000; thinking counts). |
| **Enabled** | A disabled agent is never planned for. |
| **Version** | Read-only: the number of the saved definition ([Versions](#versions)). |

## Tools

- **Only read-level tools.** A department agent answers inside one question and cannot pause for
  a person's approval, so tools with `write` or `external` side effects (`send_email`,
  `create_task`, `web_search`, MCP tools not marked read-only) are refused with a `422
  tool_not_allowed` that says so. Use research mode for actions: there each call waits in the
  approvals inbox. `search_knowledge` is refused too: searching is built in, and as a tool it
  would search outside the agent's collections. `GET /api/v1/admin/tools` lists every tool with
  `for_agents` and, if false, the reason; the editor shows the others disabled with that reason.
- **As the person asking.** Every call goes through `execute_tool` with the asker's request
  context: their scopes, the tool's own permission checks, input validation, sanitized and
  size-limited output, and an audit event `tool.called` with `caller: agent:<key>`. A tool the
  person lacks the scope for is not offered. The read-level check is repeated on every run, so
  an MCP tool re-imported as write-level stops being offered without anyone editing the agent.
- **When.** After its first search, an agent with tools sees what it found and may call its
  tools, at most `max_tool_calls` times (further calls are refused and recorded). Tool outputs
  are passed on as `<tool_result>` elements (markers `T1`, `T2`, …) that findings may cite; the
  composer says which tool a fact came from. They are data, never instructions.
- **Caveat.** Tool output has no sensitivity label. Under a `local_only` policy, restricted
  *document passages* still never reach an external model, but tool output goes to the model
  that runs the agent, as it does in research mode.

## Model

An agent stores a **model catalog id** ([docs/models.md](models.md)) or nothing:

- nothing: the model the organization maps to the `agent` role in **Admin › Models**, else the
  deployment's (`LLM_MODEL_AGENT`, else `LLM_MODEL`);
- `system:<model>`: that model on the deployment's own provider, for example
  `system:claude-opus-5-5` (`system-local:<model>` for the deployment's local model);
- `<provider id>:<model>`: a model of a provider an administrator added (bring-your-own key or a
  local server).

**When it is saved** (create, update, restore, and a draft in the test console), the id must
be an enabled model of an enabled provider in the organization's catalog: otherwise `422
unknown_model` (the catalog has no such model) or `422 model_disabled` (it or its provider is
disabled). From then on, Admin › Models refuses to delete or disable that model or its provider
(`409 model_in_use`, "the agent 'Legal agent'") until no agent uses it.

**When it runs**, the agent's own calls (the tool loop and extracting findings) go through the
organization's model resolution with every role on that model, in chat and in the test console
alike; planning keeps the `router` role's model and the final answer the `answer` role's. If the
model became unusable anyway (the deployment's own model list changed), that agent's run fails
("model unavailable") instead of quietly using another model; the answer then uses the other
agents or the plain search, and says what is missing. Sensitivity routing is unchanged: if
restricted sources must stay local, they go to the local model whatever the agent's model is.

The editor fills the model select from `GET /api/v1/admin/models`, the same query as
Admin › Models: *Default for its role* (naming the model that is), then the enabled models of
enabled providers. A saved model the catalog no longer offers stays selectable, marked, so
opening and saving an agent never changes its model.

## Versions

Every create, update and restore writes a row in `agent_definition_versions` (forced row-level
security, like every tenant table): the full definition as a snapshot, the author, an optional
"what changed" note, and the time. **Restoring copies an old snapshot into a new version**;
history is never rewritten. A restore is checked against today's collections and tools, so a
version naming a deleted collection can't be restored until it is fixed.

- Definitions that existed before migration `0013` get version 1 with the note *Definition
  before versioning*.
- `make seed` adds a version (author: none, note *Seeded from company.yaml*) only when
  `company.yaml` changed an agent; re-seeding an unchanged file adds nothing. Seeding still
  overwrites the fields the file sets (name, description, instructions, collections, examples,
  searches) and leaves tools, model, limits and *Enabled* alone; the version history shows what
  it overwrote, and *Restore* brings an edit back.
- Deleting an agent deletes its versions. The audit log keeps `admin.agent.created`,
  `.updated` (with the changed field names), `.restored` and `.deleted`.

In the editor, **Versions** lists the history (author, time, note), shows a version's
differences from the current definition field by field, and restores it after a confirmation.

## Test console

The **Test console** tab runs the definition *as it is in the form*, saved or not:

1. **Routing.** The real planner (and fast path, if on) plans the question over the
   organization's enabled agents, with the draft in place of the saved agent of the same key,
   or added if new. It shows which agent(s) were picked, by what (fast path, planner), the goal
   each received, and whether the tested agent was among them.
2. **Answer.** The department agent runs with the draft definition, searching **as the
   administrator**: their permissions, narrowed to the draft's collections. An organization
   administrator does not see restricted collections they are not granted (Erin, Acme's IT
   administrator, gets nothing from *Employee Records*). The answer shows its citations, the
   searches and tool calls, and the tokens and cost of every model call.
   If the planner did not pick the agent, it still answers the question itself, so you see what
   it would have said.

Nothing is stored as a conversation. Each run is audited as `agent.test` (draft or saved,
plan, cited documents, tokens). Model calls are real and cost money: they are recorded in usage
as feature `agent_test`, for the administrator and the tested agent, and a used-up budget
refuses the run with `429 budget_exceeded` before it starts ([docs/usage.md](usage.md)).

## How routing works

1. **Fast path** (on by default, `AGENT_FAST_PATH`). The question is embedded and compared with
   each enabled agent's profile (name, description, example questions). If the best agent scores
   at least `AGENT_FAST_PATH_MIN_SCORE` (0.3) and leads the runner-up by
   `AGENT_FAST_PATH_MIN_MARGIN` (0.15), it gets the question with no model call.
2. **Planner.** Otherwise the `router` model gets each agent's key and description and returns a
   plan: `direct`, `single` or `fan_out` (at most 4 subtasks). `direct` is only for questions
   that need none of the company's documents (a greeting, thanks, a general writing request);
   it runs the plain assistant. Unknown keys, duplicates and empty goals are dropped before
   anything runs.
3. **Freshness.** Definitions are read from the database for every question, so a created,
   edited or disabled agent takes effect on the next question in every API process and worker.
   The fast path caches profile embeddings by the profile's *text*: an edited description or
   example is embedded again on the next question, and the cache is bounded (least recently used
   out first). Tested in `tests/integration/test_agent_config_api.py` with two independent
   routers standing for two processes.

Good descriptions decide routing. Name the topics, and the neighbouring topics the agent is
*not* for, especially where words overlap: "contract" appears in HR (employment contracts),
Sales (customer renewals) and Legal questions.

## Create the Legal agent, step by step

Acme's legal documents are in the **Legal & Contracts** collection (`legal-contracts`): 10
documents seeded from `seed/acme/corpus/legal-contracts/` (signature authority matrix, NDA
policy, supplier contract checklist, DPAs and GDPR, standard customer terms, export control,
records retention and litigation hold, IP and invention assignment, anti-bribery and gifts,
working with Legal). Every member can read it; guests can't. The agent itself is **not
seeded**: this is the Phase 11 check that a new agent needs no code.

1. Start the stack and seed it (`make up && make seed`), sign in at <http://localhost:8480> as
   `erin@acme.test` (organization administrator, password `needwise-demo`), and open
   **Admin › Agents › New agent**.
2. Fill in:

   | Field | Value |
   |---|---|
   | Name | `Legal agent` (the key follows: `legal`; a trailing "agent" is left out of the suggested key. Before 2026-11-12 the editor suggested `legal-agent`: check the **Key** field, the evaluation looks for `legal`) |
   | Description | `Contracts and compliance: who may sign which contract and up to what value (the signature authority matrix), NDAs and when the standard NDA needs no legal review, supplier contract reviews, data processing agreements and GDPR, the standard customer terms (payment terms, liability cap, warranty), export control and sanctions for robot shipments, records retention and litigation holds, IP and invention assignment, and anti-bribery rules for gifts and hospitality to customers and public officials. Not for employment contracts, probation, leave or pay (HR), or a customer's renewal date or deal status (Sales).` |
   | Collections | Legal & Contracts |
   | Example questions | `Who signs a customer contract worth more than one million euros?` · `When can I sign an NDA without a legal review?` · `Does a supplier that processes personal data need a DPA?` · `Which countries can't we ship robots to?` · `What are the rules for gifts to public officials?` |
   | Tools | none |
   | Model | Default for its role |
   | Limits | 2 searches, no tool calls, 6,000 output tokens |
   | Instructions | `Quote thresholds, deadlines and owners exactly as the documents state them. If a question needs legal advice beyond the documents, say so and point to legal@acme.test.` |

   The example questions are deliberately different from the evaluation's questions, so the
   evaluation measures routing rather than recall of its own wording.
3. Open **Test console** and ask, before saving:
   - *Can I sign a 30,000 euro supplier contract myself?* Expect: **Legal agent**; the answer
     says the CFO signs supplier contracts from 25,000 to 150,000 euros, citing *Contract
     Signature Authority Matrix*.
   - *How long is the probation period in my employment contract?* Expect: **HR agent**, and the
     note that the planner did not pick this agent.
   - *When does the Bluefin Freight contract renew, and what liability cap do our standard terms
     set?* Expect: **Legal agent and Sales agent**.
   If routing is wrong, sharpen the description (what it is *not* for) and test again.
4. Write a note in **What changed** (e.g. *First version of the Legal agent*) and **Create
   agent**. It answers in chat from the next question on (`ASSISTANT_MODE=agents` or `auto`).
5. Check routing with the evaluation, from `apps/api`:

   ```bash
   uv run nw eval --skip-retrieval --suites routing --concurrency 3 --label 2026-11-12-routing-legal
   ```

   (`uv run nw eval --suites routing` also runs the retrieval checks first.) Before the agent
   exists, the 15 questions that expect it are reported as *skipped (agent missing: legal)* and
   not counted. Afterwards they run, and the report (`evals/results/<label>.md`) has a per-agent
   table: questions that expect the agent, exact plans, how often it was picked, and how often it
   was picked when it should not have been (near misses: employment-contract questions that must
   go to HR).

## Measured: routing with the Legal agent

On 2026-11-12 the Legal agent was created through the API as `erin@acme.test` with exactly the
fields above, the three console questions of step 3 routed as described (Legal by the fast path;
HR, with the note that the Legal agent was not picked; Legal and Sales), and the routing
evaluation ran against the seeded stack (56 questions, 3 at a time, fast path on):

| Run | Planner | Exact plans | Legal: picked / exact | Near misses kept away | Report |
|---|---|---|---|---|---|
| Before the planner fix | Sonnet 5.5 | 53/56 | 15/15 / 14/15 | 3/3 | [`2026-11-12-routing-legal`](../evals/results/2026-11-12-routing-legal.md) |
| **After the planner fix** | **Sonnet 5.5** | **53/56** | **15/15 / 14/15** | **3/3** | [`2026-11-12-routing-legal-after-fix`](../evals/results/2026-11-12-routing-legal-after-fix.md) |
| After the fix | Opus 5.5 | 54/56 | 15/15 / 14/15 | 3/3 | [`2026-11-12-routing-legal-opus`](../evals/results/2026-11-12-routing-legal-opus.md) |
| **Agent created in the browser** | **Sonnet 5.5** | **53/56** | **15/15 / 14/15** | **3/3** | [`2026-11-12-routing-legal-ui`](../evals/results/2026-11-12-routing-legal-ui.md) |

- **The miss on legal questions** is the same in all three runs: *"I came up with a new charging
  technique at work. Do I need to report it, and is there a bonus if it gets patented?"* goes to
  Legal **and HR** (a bonus sounds like pay). Nothing was lost (Legal was among the agents), it
  costs one extra agent. The Legal agent was never picked for a question that did not need it.
- **The other agents.** On the 36 questions measured on 2026-11-04, Sonnet 5.5 scores 34/36 after
  the fix, with the same two misses as the 2026-11-04 Sonnet run
  ([`2026-11-04-sonnet-baseline`](../evals/results/2026-11-04-sonnet-baseline.md)): an extra
  Engineering agent for the engineering budget, an extra Finance agent for cost per lead. Opus 5.5
  scores 35/36 (36/36 on 2026-11-04,
  [`2026-11-04-routing`](../evals/results/2026-11-04-routing.md)); its miss adds Engineering to
  *"What is Project Heron?"*.
- **What the evaluation caught.** Adding the Legal agent changed an unrelated plan: *"What is
  Project Heron?"* went to `direct` (no agent) in 5 of 5 plans with the Legal agent listed, and to
  Leadership in 5 of 5 without it. The planner's rule for `direct` read "general, chit-chat, or not
  tied to any area above", and an unknown project name fits that literally. The rule now says
  the question needs none of the company's documents. Planner only, two plans per question:
  104/112 exact before, 106/112 after, with no new miss. A longer wording that also told the
  planner to route names it does not recognise fixed Project Heron but added an agent to two
  marketing questions (52/56, [report](../evals/results/2026-11-12-routing-legal-rejected-rule.md));
  it was not kept. The dataset was not changed.
- Planner calls are not deterministic: one run moves single items, as the 2026-11-04 runs did.

The API-made Legal agent was deleted afterwards, so that it could be created again in the UI.

**Created in the browser (the Phase 11 DoD).** Later on 2026-11-12 `erin@acme.test` created the
Legal agent in **Admin › Agents › New agent** with the fields of step 2, tried it in the test
console before saving, and saved it (the browser walkthrough also changed a field and restored
version 1, so it is at version 3 with the same content). The editor suggested the key
`legal-agent`; it was changed to `legal` by hand (the suggestion is fixed now). The routing
evaluation against that agent
([`2026-11-12-routing-legal-ui`](../evals/results/2026-11-12-routing-legal-ui.md), commit
`d43225e`, Sonnet 5.5 planner, 3 at a time) gave the same result as the API-made agent: 53/56
exact plans, Legal picked for 15/15 legal questions with 14 exact plans, never picked when not
expected, 3/3 near misses kept away from Legal. The three wrong plans are the same three: the
invention and patent-bonus question (Legal and HR), the engineering budget (Engineering and
Finance) and cost per lead (Finance and Marketing). 10 questions took the fast path (all
correct); the other 46 each made one planner call, and all 46 were recorded in usage as feature
`eval` with no person: 57,069 input and 4,245 output tokens, $0.157.

## The routing dataset

`evals/datasets/routing.jsonl` (56 questions): single-area questions for every Acme agent (at
least 3 each for the configuration-only Support, Sales and Marketing agents), 10 single-area
legal questions phrased the way employees ask, 5 that need Legal with Finance, Sales or HR, 3
direct questions, cross-department questions, and near misses that name the agent they sound
like in `not_agents` (they must not go there). Agents defined in `seed/acme/company.yaml`
(HR, Engineering, Finance, Sales, Support, Operations, Marketing, Leadership) need no code
either; the Legal agent is the one created through the UI.

## API

All under `/api/v1/admin` (organization owners and administrators):

| Method and path | What it does |
|---|---|
| `GET /agents` | Every agent, enabled or not, with `version`. |
| `POST /agents` | Create (`key`, the fields, optional `note`). Version 1. |
| `PUT /agents/{key}` | Replace the fields (optional `note`). A new version. |
| `DELETE /agents/{key}` | Delete it and its versions. |
| `GET /agents/{key}/versions` | The history, newest first: version, author, note, time, snapshot. |
| `GET /agents/{key}/versions/{n}` | One version. |
| `POST /agents/{key}/versions/{n}/restore` | A new version equal to version `n` (optional `note`). |
| `POST /agents/test` | `{question, draft?, key?}` → `{routing: {mode, agents, routed_by, reason, tested_agent_picked}, answer: {text, citations, usage, steps, …} \| null}`. |
| `GET /tools` | Every registered tool, its side-effect level, and whether an agent may have it. |

Errors: `422 unknown_collection`, `unknown_tool`, `tool_not_allowed`, `unknown_model`,
`model_disabled`, `validation_error` (a malformed model id, limits out of range), `409
slug_taken`, `404 not_found`.

## Not built and not measured

- **An agent on a model other than the default** has been tested with scripted models only
  (the routing evaluation measures the planner, which uses the `router` role, not the agents'
  own models).
- Agent tools are offered in agents mode only; the planner does not see an agent's tools when it
  plans, only its description.
- The test console runs synchronously (no streaming); a slow model makes the request wait up to
  the agent timeout (90 s).
