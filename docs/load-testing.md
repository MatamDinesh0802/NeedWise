# Load testing

A Locust harness that loads WorkMind the way people use it: browsing, searching, asking and
streaming answers, the agents, insights, polling, uploads and sign-ins. A stub model server
answers in place of a real model, so a run measures WorkMind's own overhead (retrieval,
permission checks, the database, Valkey, the event stream, Keycloak) and costs nothing.

Code: `loadtests/`, its own uv project with Locust pinned (2.47.0). The stub is
`loadtests/stub_llm.py`; the proof that WorkMind's model adapter works against it is
`apps/api/tests/test_stub_llm.py`. Reports go to `loadtests/results/`.

This page says how to run it, what each scenario does, how the stub behaves, how to read a
report, what a run leaves behind, and what has been measured so far. The benchmark (10 to 800
users on the main stack, before and after tuning) and what it changed are in
[load benchmarks](benchmarks/load.md).

## Quick start

```bash
cd loadtests && uv sync && cd ..

# No model calls: safe against any stack, including one that answers with a paid model.
make loadtest USERS=5 DURATION=60s NO_MODEL=1 LABEL=smoke

# Everything, with the stack's model calls going to the stub (next section first):
make loadtest USERS=50 SPAWN_RATE=5 DURATION=10m STUB_URL=http://127.0.0.1:8490 LABEL=stub-50
```

`make loadtest` runs `python -m workmind_load.run`, which:

1. checks the target (`/api/v1/system/status`) and, unless `NO_MODEL=1`, signs in as the org
   admin and refuses to start unless every model call of the demo organization would reach the
   stub (see "Preflight");
2. creates one access token per demo login (`tokens.py`);
3. runs Locust headless, writing CSV, JSON and HTML to `loadtests/results/raw/<run>/`
   (git-ignored);
4. always cleans up: each Locust user deletes the conversations it created when it stops, then
   every remaining `[load] ` conversation is deleted and every token revoked;
5. writes the report `loadtests/results/<UTC date>-<label>.md`.

| Make variable | Default | |
|---|---|---|
| `USERS`, `SPAWN_RATE`, `DURATION` | 10, 1, 2m | Locust's `-u`, `-r`, `-t` |
| `LABEL` | run | Names the report |
| `MIX` | see "Scenarios" | Class weights, e.g. `reader=10,chat=3,upload=0` |
| `NO_MODEL` | off | `1`: leave out every task that calls the model |
| `STUB_URL` | none | The stub's URL, to count its calls in the report |
| `UPLOAD_COLLECTION` | none | Collection key for the upload scenario; unset: no uploads |
| `THINK` | `3,10` | Seconds between a person's actions (min,max) |
| `PROCESSES` | 1 | Locust processes; `-1`: one per CPU |
| `MONITOR` | none | A compose project's name (`workmind`): record its resource use for the report (see "Resource use") |
| `LOADTEST_HOST` | `http://localhost:8480` | The target |

Other targets: `make loadtest-stub` (the stub; `STUB_PORT`, `TTFT_MS`, `TPS`, `ANSWER_TOKENS`),
`make loadtest-report RUN=<date>-<label>` (rewrite a report from its raw statistics),
`make loadtest-sweep` (revoke leftover tokens, delete leftover conversations and uploads), and
`make loadtest-check` (the harness's lint, types and tests; no stack needed).

Locust's web UI works too: `uv run python -m workmind_load.tokens create`, then
`uv run locust --host http://localhost:8480` and open http://localhost:8089; afterwards
`uv run python -m workmind_load.tokens revoke`. Every `--wm-*` option below also reads its
environment variable.

| Locust option | Variable | |
|---|---|---|
| `--wm-tokens` | `WM_LOAD_TOKENS` | Token file (default `loadtests/.secrets/tokens.json`) |
| `--wm-mix` | `WM_LOAD_MIX` | Class weights |
| `--wm-no-model` | `WM_LOAD_NO_MODEL` | Leave out tasks tagged `model` and the classes that only ask |
| `--wm-think` | `WM_LOAD_THINK` | `min,max` seconds between actions |
| `--wm-datasets` | `WM_LOAD_DATASETS` | Question sets (default `evals/datasets`) |
| `--wm-answer-timeout` | `WM_LOAD_ANSWER_TIMEOUT` | Seconds before an answer counts as timed out (180) |
| `--wm-follow-up` | `WM_LOAD_FOLLOW_UP` | Share of chat questions asked in the same conversation (0.3) |
| `--wm-upload-collection` | `WM_LOAD_UPLOAD_COLLECTION` | Collection key for uploads |
| `--wm-uploader`, `--wm-admin` | `WM_LOAD_UPLOADER`, `WM_LOAD_ADMIN` | Logins for those scenarios (erin) |
| `--wm-password` | `DEMO_PASSWORD` | The demo logins' password (`workmind-demo`) |

## Point the stack at the stub

The stub runs on the host; containers reach it at `host.docker.internal`.

```bash
make loadtest-stub                                  # terminal 1: 127.0.0.1:8490
docker compose -f compose.yaml -f loadtests/compose.stub.yaml up -d --wait api worker beat
(cd loadtests && uv run python -m workmind_load.preflight --expect-model workmind-stub)
make loadtest USERS=50 SPAWN_RATE=5 DURATION=10m STUB_URL=http://127.0.0.1:8490 LABEL=stub-50
docker compose up -d --wait api worker beat         # back to the model in .env
```

`loadtests/compose.stub.yaml` overrides, for `api`, `worker` and `beat` only:

| Setting | Value | Why |
|---|---|---|
| `LLM_PROVIDER` | `openai_compatible` | The adapter the stub speaks to |
| `LLM_BASE_URL` | `http://host.docker.internal:${STUB_PORT:-8490}/v1` | The stub |
| `LLM_MODEL`, `LLM_MODEL_<ROLE>` | `workmind-stub` | Every role, so per-role models in `.env` do not leak through |
| `LLM_API_KEY` | empty | The stub needs none |
| `LLM_EXTERNAL` | `false` | The stub counts as a model on your own infrastructure, as a local model would: restricted sources are not withheld or rerouted (ARCHITECTURE 11.6). Set `true` to load the routing path instead. |
| `RATE_LIMITS_ENABLED` | `false` (`LOADTEST_RATE_LIMITS_ENABLED`) | Many simulated users share nine demo logins and one client address, so the per-person, per-tenant and sign-in request limits ([multi-tenancy](multi-tenancy.md#abuse-limits)) would answer `429 rate_limited` and the run would measure the limits, not capacity |
| `MAX_INFLIGHT_PER_USER` | `100` (`LOADTEST_MAX_INFLIGHT_PER_USER`) | The same for the cap of 4 answers in progress per person (`429 too_many_requests`); 100 is the highest value the setting accepts |

The same lines in `.env` work as well; then restart `api`, `worker` and `beat`. To load-test
with the production limits, start the override with `LOADTEST_RATE_LIMITS_ENABLED=true
LOADTEST_MAX_INFLIGHT_PER_USER=4`; refusals then show as failures in the report.

**What else decides which model is called.** The deployment's settings are only the defaults
of each organization's model catalog ([docs/models.md](models.md)). An organization keeps its
own choices:

- a role mapped to the organization's own provider (Admin › Models › Roles) stays on that
  provider, real key included. Set the roles back to "Deployment default" for the run
  (`PUT /api/v1/admin/models/roles` with `null`s);
- a role mapped to a deployment model that no longer exists (`system:claude-sonnet-5-5` once the
  deployment serves `workmind-stub`) falls back to the default: the stub;
- an agent with its own model (Admin › Agents) does not fall back: with that model gone it fails
  ("model unavailable"); with a model on the organization's own provider it calls that provider.
  Clear the agent's model for the run, or expect its failures;
- the data policy's local model receives restricted sources when the answering model is external;
  with `LLM_EXTERNAL=false` the stub is not external, so this does not happen.

**Preflight.** `python -m workmind_load.preflight --expect-model workmind-stub` signs in as the
org admin, reads Admin › Models and Admin › Agents (keys are never returned) and fails on each
of the cases above: a deployment provider that is not `openai_compatible`, any role not on
`system:workmind-stub`, a data-policy local model, an enabled agent with another model.
`make loadtest` runs it before any question is sent and stops on a problem. `--allow-real-model`
(run.py) skips the check; then questions go to whatever model is configured.

**Budgets.** The stub's model has no price, so its calls are recorded as unpriced usage events
and do not use budgets up ([docs/usage.md](usage.md)). A budget that is already used up still
refuses questions (`429 budget_exceeded`), which shows as failures.

**Assistant mode.** `ASSISTANT_MODE` (compose `.env`) decides what a chat question does:
`single` searches and answers; `auto` asks the planner first for every question (unless the
embedding fast path picks an agent) and runs department agents when the plan spans two areas;
`agents` runs agents for every planned question. The "agents" scenario sends cross-department
questions: they exercise the agents only in `auto` or `agents` mode. The mode is not visible
through the API; add it to the report with `--note` (run.py).

**Linux.** compose maps `host.docker.internal` to the host (`host-gateway`), but a server bound
to 127.0.0.1 is not reachable from containers there: start the stub with `--host 0.0.0.0` (or
the Docker bridge address) and keep the port firewalled, since the stub has no authentication.
On Docker Desktop (macOS, Windows) the default 127.0.0.1 works.

**Embeddings stay real.** The stub serves no embeddings: search, the agents' fast path and
ingestion keep using `EMBEDDING_*`, so retrieval costs what it costs in production.

## Scenarios

Each kind of person is a Locust user class (`workmind_load/users.py`); Locust spreads `USERS` over
the classes by weight. With few users a class with a small weight may get none: give it a
`MIX` weight of its own (the smoke run below used one user per class).

| Class (`MIX` key) | Signs in with | Does | Model | Weight |
|---|---|---|---|---:|
| `Reader` (`reader`) | access token | Chat list and one conversation, collections, a collection's documents, a document, search, `/me`, the agents list, the usage summary (last 30 days) | no | 10 |
| `Poller` (`poller`) | access token | An open tab: notifications and pending approvals every 30 s (the web app's bell interval) | no | 6 |
| `Asker` (`chat`) | access token | A question from `retrieval_qa.jsonl` in a new conversation (30 % as a follow-up in the same one), the answer's event stream read to the end, then the conversation | yes | 3 |
| `AgentsAsker` (`agents`) | access token | Cross-department questions (`cross_department.jsonl`); one in four is a research-mode question (tool loop) | yes | 1 |
| `Analyst` (`analyst`) | access token | An insights question (`analytics.jsonl`), the data model, past answers, and re-running the SQL that answers one of the person's questions | ask only | 2 |
| `Admin` (`admin`) | browser session | People, audit log, usage by department, models, collections, agents | no | 1 |
| `Login` (`login`) | Keycloak | The whole sign-in (WorkMind → Keycloak email page → password → callback), `/me`, sign-out | no | 1 |
| `Uploader` (`upload`) | browser session | Uploads a 2 KB Markdown file, polls until it is `ready` (parsed, chunked, embedded, indexed), deletes it | no | 1 |

**Who asks.** Most users use access tokens, as scripts and MCP clients do: the API serves token
and session requests through the same handlers, with the token's scopes (`profile:read`,
`knowledge:read`, `assistant:ask`). Uploads, admin pages and creating tokens accept only a
browser session, so those classes sign in through Keycloak first (`browser.py`). Users are
spread over the nine demo logins round-robin; analysts only over logins with answerable insights
questions (carol, dan, priya.raman, hannah.schmidt). With more users than logins, several users
act as one person in several tabs: per-person limits (one running answer per conversation) and
caches behave accordingly.

**Questions** come from `evals/datasets`, so answers hit real documents: each login asks its own
questions where the dataset names it as the asker, and any of the set's questions otherwise.

**Request names** are `<area>: <action>` (`chat: send question`, `documents: list`,
`login: submit password`). Custom metrics record what a person waits for, timed from their
action:

| Type | Name | From | To |
|---|---|---|---|
| SSE | `<chat\|agents\|research>: time to sources` | sending the question | the `retrieval` event (search done; research mode searches inside its tool loop and has none) |
| SSE | `…: time to first token` | sending the question | the first `token` event |
| SSE | `…: answer complete` | sending the question | `run.completed` (a `run.failed`, a closed stream or the answer timeout is a failure) |
| INGEST | `upload: time to ready` | the upload | the document's status `ready` |
| FLOW | `login: whole flow` | `/api/auth/login` | the session cookie |

A failed metric is timed until the failure was noticed, not recorded as 0 ms.

## The stub

`loadtests/stub_llm.py` (Starlette and Uvicorn, both already in `apps/api`) answers the four
request shapes of `OpenAICompatibleLLM` (`apps/api/src/workmind/providers/llm.py`):

| Request | Answer |
|---|---|
| `stream: true` | SSE chunks, one word each, then a usage chunk (`stream_options.include_usage`) and `data: [DONE]`. A prompt with `<source id="S1" …>` elements gets one sentence per source (up to three), each cited `[S1]`, padded to `--answer-tokens` words. No sources: the abstention sentence. |
| plain | A question rewrite ("Latest message: …") comes back unchanged; a conversation summary is one sentence; a prompt with sources gets a cited answer; anything else (the provider test) "OK". |
| `response_format.json_schema` | `PlanOut`: agents from the planner's list whose description shares words with the question (two when the question says "and" and two match; none for a greeting). `EvidenceOut`: one finding per source, citing it. `Composed`: a narrative citing `[Q1]` that copies one value of the result, so number verification passes; no chart for a single value. Any other schema: a minimal valid instance. |
| `tools` | First turn: `search_knowledge` with the question (research mode), or `run_sql` with a `COUNT(*)` over the first table of the analytics data model. After a tool result: a final answer. Other tools are never called (`--no-tool-calls`: none at all). |

Answers are deterministic: the same request gets the same text.

**Timing.** Every response waits `--latency-ms` (20) before it starts. A stream then waits
`--ttft-ms` (600) for its first word and sends `--tokens-per-s` (60) words per second; a
non-streamed reply arrives after latency + ttft + its words / tokens-per-s, as if generated in
full. `--answer-tokens` (120) sets the length of streamed answers, so with the defaults a cited
answer streams for about 2.6 s. `--jitter 0.2` varies each delay by up to ±20 % (seeded);
`--error-rate 0.05` answers 5 % of requests with HTTP 503. Usage counts four characters per
prompt token and one word per output token.

`GET /stats` reports calls by kind (and structured outputs by schema), errors, calls in flight
and the peak since the stub started; `run.py` reads it before and after a run for the report.
`GET /v1/models` and `GET /healthz` exist too. The stub has no authentication and no limits.

**Tests.** `apps/api/tests/test_stub_llm.py` runs WorkMind's own adapter against the stub, in
process: a streamed answer through `answer_stream` comes back word by word with both sources
cited and no unknown markers; a follow-up rewrite; the agents pipeline end to end
(`orchestrate`: a fan-out plan, both department agents with findings, a cited composed answer);
a one-area plan and a greeting; the research tool loop (one `search_knowledge` call, then an
answer); the analytics agent on the Acme dataset (a `COUNT(*)` query whose number verifies);
the meetings extraction, judge and plan schemas; the admin provider test; and the timing over a
real socket (first token after latency + ttft, then the configured rate).
`loadtests/tests/test_stub.py` covers the stub's own edges. Building these tests found a bug in
WorkMind: a single value charted as a table failed the whole insights answer
(`_chart` raised `IndexError`); it is fixed, with a test.

## Access tokens and what a run leaves behind

`python -m workmind_load.tokens` (also used by `run.py`):

| Command | |
|---|---|
| `create` | Signs in as each demo login (Keycloak) and creates a token as Settings › Access tokens does (`POST /api/v1/me/tokens`): name `loadtest-<UTC time>`, the three grantable scopes, 1 day (the shortest lifetime, so a forgotten token expires tomorrow). Plain tokens go to `loadtests/.secrets/tokens.json` (mode 0600, git-ignored) and are never printed. |
| `check` | `GET /api/v1/me` with each token |
| `revoke` | Deletes each login's `[load] ` conversations with its token, then signs in and revokes the tokens, then deletes the file. A failure keeps the file, so it can be run again. |
| `sweep` | Without the file: for each login, revokes every unrevoked token named `loadtest-*` and deletes `[load] ` conversations (with a cleanup token it revokes at once); with `--upload-collection`, also `loadtest-upload-*` documents |
| `upload-collection` | As the org admin, creates the restricted collection `loadtest-uploads` that only the uploader (erin by default) can see and edit, unless it exists |

What a run creates and removes: tokens (revoked), conversations titled `[load] …` (deleted),
uploaded documents (deleted once indexed; `sweep` catches leftovers), browser and Keycloak
sessions (signed out). What stays, by design or for lack of an API:

- usage events, one per model call (unpriced with the stub), in usage reports and analytics;
- audit events (tokens created and revoked, answers, admin reads): the audit log is append-only;
- insights answers in each analyst's history (there is no API to delete them);
- the `loadtest-uploads` collection, empty (there is no API to delete a collection);
- run event streams in Valkey, which expire an hour after the run.

## Reading a report

A report (`loadtests/results/<date>-<label>.md`) has:

- **the run**: target, the harness's commit (the target may run other code), users, spawn rate,
  duration, the effective class weights, the model (none, or the stub and its timing) and the
  load generator;
- **summary**: HTTP requests, failures and throughput over the measured time; the three slowest
  endpoints at p95; time to first token per scenario;
- **requests**: per endpoint, requests, failures, requests per second, p50, p95, p99 and max in
  milliseconds, and "All HTTP requests" over every endpoint (custom metrics left out);
- **what people waited for**: the custom metrics above;
- **failures**: Locust's failure table, most frequent first;
- **calls to the stub**: by kind and schema, to check that a run used the stub and how many
  model calls a scenario makes (in `auto` mode, a planner call for each chat question the fast
  path does not route);
- **resource use**, with `MONITOR` (below);
- **the target before the run**: the preflight's findings; **notes** given with `--note`.

Percentiles are Locust's, from its histogram: times are rounded to 10 ms above 100 ms and to
100 ms above 1 s, so "2,000" means 1,950 to 2,049 ms. A percentile over few requests is just the
slowest one: read p99 only where an endpoint has hundreds of requests.

**WorkMind's own share** of an answer's time is the measured time minus the stub's configured
delays of the calls made before it. With the defaults, a single-mode answer's first token
cannot come sooner than 620 ms (one streamed call); a follow-up adds its rewrite (latency + ttft
+ the rewrite's words at 60/s); `auto` and `agents` add the planner and, for two-area plans, the
department agents. "Time to sources" ends before the answering call starts, so with the
single mode it is all WorkMind (plus a rewrite for follow-ups). The report does not subtract
these yet: the stub counts calls but not their durations per run.

Compare runs only with the same machine, stub timing, mix, think time and assistant mode.

### Resource use

`MONITOR=<compose project>` (`--monitor`, `workmind_load/monitor.py`) records what the stack spent
while Locust ran, from the host with the Docker CLI only:

- every 5 s, `docker stats` for each container of the project (CPU in % of one core, memory) and
  Postgres connections per database and state, with the number of active backends waiting on a
  lock;
- the API's own time per route from its access log (`docker logs`): without Caddy and the
  network, ids replaced by `{id}`;
- the Postgres statements with the most total time, from `pg_stat_statements`. The monitor
  resets the statistics when the run starts. The extension must be loaded by the server
  (`shared_preload_libraries=pg_stat_statements`) and created in the `postgres` database; the
  compose file does neither, so without it the report says so;
- Qdrant's requests and latency per endpoint during the run (its `/metrics` histogram;
  `--qdrant-url`, default `http://localhost:6333`), and Valkey's commands per second.

It runs `psql` as the `postgres` superuser inside the Postgres container: it is for test stacks.

## Caveats

- **Locust and the stack on one machine** compete for CPU and memory, as the reports below did.
  One Locust process uses one core; when Locust warns about its CPU, use `PROCESSES=-1`.
  With several processes, Locust 2.47 writes an empty JSON file and the report reads the CSV,
  which Locust rewrites every second and can miss the final second.
- **Think times** (3–10 s by default) make throughput per user low and realistic; set `THINK`
  lower to find limits.
- **Stub answers are not real answers**: short, always cited, never refused, verified numbers.
  Retrieval, permissions, storage, streaming and ingestion are real.
- **Caddy compresses** responses for Locust (gzip) as it does for browsers; the event stream is
  read as it arrives.
- **The API only**: pages rendered by the web app (Next.js), the MCP endpoint, meeting uploads,
  workflow runs and approval decisions are not loaded.
- **Shared logins**: nine demo logins carry all users (see "Who asks"). This is why
  `compose.stub.yaml` lifts the abuse limits; with `NO_MODEL=1` and no override, a run against an
  unchanged stack keeps them, and a large one meets `429 rate_limited`.

## Measured so far

Two validation runs of the harness, on a laptop (Apple Silicon, 10 cores) running Docker
Desktop and Locust side by side, with other work running at the same time. They show the
harness works; they are not performance results.

| Run | Target | What ran | Result |
|---|---|---|---|
| [2026-11-12 smoke](../loadtests/results/2026-11-12-1737-smoke-shared-stack.md) | The development stack (Claude as the model), 5 users, 60 s, model calls off, one user per class | reader, poller, analyst (no asking), admin, login | 61 HTTP requests, 0 failures; 4 whole sign-in flows; tokens for the 9 demo logins created and revoked, a sweep afterwards found nothing left |
| [2026-11-12 throwaway stack](../loadtests/results/2026-11-12-1730-isolated-stack-stub.md) | Its own compose project (Community edition, hash embeddings, `ASSISTANT_MODE=auto`, fast path off) pointed at the stub, 15 users, 2 min | every class | 464 HTTP requests, 0 failures; 33 chat, 20 cross-department and 3 research answers streamed (chat time to first token p50 1,700 ms, p95 3,300 ms); 4 insights answers; 6 uploads indexed and deleted (time to ready p50 1,100 ms); stub calls: 53 streams, 65 structured outputs, 14 tool-loop turns, 7 plain |

The harness's own checks (`make loadtest-check`): ruff, pyright and 43 tests, among them a few
seconds of headless Locust against a fake WorkMind API that checks the token users' scenarios,
their custom metrics and that each user deletes its conversations. `--processes 2` was checked
against the throwaway stack.

The benchmark on the main stack (Enterprise dev license, real embeddings, `ASSISTANT_MODE=auto`,
the stub model) ran 10, 25, 50, 100, 200 and 400 users before tuning and the same steps plus 800
after, 3 minutes each, with `MONITOR=workmind`: [load benchmarks](benchmarks/load.md). It found
the API's single process saturated at 400 users and led to batched token events and two API
processes by default. It also found three harness bugs, now fixed: readers opened documents the
uploader had deleted (a correct 404 counted as a failure), Locust's worker processes outlived a
multi-process run and reported to the next run's master, and the monitor read Qdrant's average
gauge instead of its histogram sum.

## CI

`.github/workflows/loadtest.yml` runs only when started by hand (`workflow_dispatch`). The first
job checks the harness (ruff, pyright, tests; no stack). With a `host` input, a second job runs a
short load test against a deployment the runner can reach (for example the public demo), with
the demo password from the `DEMO_PASSWORD` secret, model calls off by default, sweeps anything
left behind, and uploads the report as an artifact. The stub adapter test
(`apps/api/tests/test_stub_llm.py`) runs in the main CI with the other API tests.
