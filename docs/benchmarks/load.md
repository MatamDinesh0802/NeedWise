# Load benchmarks

Measured on 2026-11-12 (UTC) on one laptop, with a stub in place of the language model. This
page says what was run, what each step measured before and after tuning, what limited the
stack, what was changed, what limits it now, and a sizing recommendation that is an
extrapolation from these numbers, not a measurement of a real deployment.

How the harness works and how to read a report: [load testing](../load-testing.md). Every
number below comes from a committed report under
[`loadtests/results/`](../../loadtests/results/2026-11-12-1905-before-100u.md).

## Summary

- **Up to 200 simulated users nothing in the stack was saturated**, before or after tuning:
  chat time to first token stayed at p50 1.6 s, p95 2.4 to 2.5 s, which is mostly the stub's
  configured model time. At 200 users the API used 30% of one core.
- **At 400 users the API's single process was the bottleneck.** Its event loop was busy 90% of
  the time, the database pool was exhausted, and search p95 went from 46 ms to 420 ms. A
  profile put 27% of the event loop in the per-token event path and 8% in access-token
  authentication.
- **Tuning:** streamed text is now sent as at most one event per 50 ms (the first piece at once),
  each event is one Valkey round trip instead of two, the event stream no longer checks for a
  disconnect per event, and the API runs two processes by default (`API_WORKERS`, Helm
  `api.workers`) with a configurable database pool (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`).
- **After tuning, at 400 users** chat time to first token was back at its unloaded p95 of 2.5 s,
  search p95 went from 420 ms to 130 ms, and Valkey commands per second fell by 56%.
- **The limit now** (800 users): both API processes are saturated, both database pools are
  exhausted, and the audit log's per-tenant lock waits 24 ms on average (1 s at worst). No
  request failed at any step.

## Setup

**Machine.** Apple M5 (10 cores: 4 performance, 6 efficiency), 24 GB RAM, macOS 27.0.1. Docker
Desktop 29.4.3; its VM had 8 CPUs and 15.6 GiB of memory. Locust, the stub model and the
embedding server ran on the same machine, outside the VM. Another project's 13 containers ran in
the same VM throughout; one of them used a full CPU core the whole time. The machine was not
otherwise idle.

**Stack.** The repository's compose stack (project `workmind`), all services, Enterprise dev
license, `ASSISTANT_MODE=auto` (the embedding fast path picks an agent when one clearly matches;
otherwise the planner decides), with `loadtests/compose.stub.yaml` (every model role on the stub,
rate limits off, in-flight cap 100) and, for measurement only, Postgres started with
`pg_stat_statements`. Embeddings were real: EmbeddingGemma-300M (Q8_0) on llama.cpp's server
on the host GPU with 4 slots. Measured alone, it embedded 139 queries per second at concurrency
1 (p50 7 ms) and about 500 per second at concurrency 8 to 32 (p50 15 to 62 ms).

**Data.** The Acme demo tenant: one tenant, 20 people, 49 documents (421 chunks), the analytics
dataset. Every simulated user acts as one of the 9 demo logins, so per-person data is small and
shared: each login's conversation list is short and each user's queries hit the same 421 chunks.

**Model stub.** Every response waits 20 ms; a stream then waits 600 ms for its first word and
sends 60 words per second; a non-streamed reply arrives after 620 ms plus its words at 60 per
second. Answers are 120 words. No jitter, no errors. A chat question in `auto` mode waits for the
model at least 1.24 s before its first token when the planner runs (the planner's reply, then
the streamed answer) and 0.62 s when the fast path picks the agent; agents-mode questions add
department agents and a composed answer.

**Load.** The default mix (reader 10, poller 6, chat 3, agents 1, analyst 2, admin 1, login 1,
upload 1), think time 3 to 10 s, 3 minutes per step, spawn rate 5 per second (10 from 100 users).
Uploads went to the restricted `loadtest-uploads` collection. One Locust process up to 100 users,
four at 200 and 400, six at 800. In the 200- and 400-user steps Locust used at most 44% of a core
in total and the stub at most 5% (sampled with `ps`).
At 10 users Locust gave no user to the agents, login and upload classes (their weight is too
small for 10 users), so those columns are empty for that step.

**Measurement.** Locust's statistics (its percentiles are rounded: 10 ms steps above 100 ms,
100 ms steps above 1 s), and the harness's resource monitor (`MONITOR=workmind`): `docker stats`
every 5 s, Postgres connections, `pg_stat_statements`, Qdrant's request histogram, Valkey's
command rate and the API's own time per route from its access log. Profiles: py-spy 0.4.1 attached
to the API process from a sidecar container, in separate runs that are not in the tables.

## Results per step

Before: commit 0888b5b plus the monitor. After: the tuning below (commits 68e1976 and 1c63c1a).
Times in milliseconds. CPU in % of one core, the API container's total (all its processes).
"DB connections" is the API's peak, with those idle in a transaction in parentheses.

| Users | | req/s | Failed | Chat first token p50 / p95 | Agents first token p95 | Search p50 / p95 / p99 | Conversations list p95 | Insights data model p95 | Upload ready p95 | Sign-in p95 | API CPU mean / peak | DB connections | Valkey cmd/s |
|---:|---|---:|---:|---|---:|---|---:|---:|---:|---:|---|---|---:|
| 10 | before | 1.6 | 0 | 1,600 / 2,400 | - | 37 / 50 / 51 | 20 | 57 | - | - | 3 / 13 | 2 (0) | 42 |
| 10 | after | 1.6 | 0 | 1,600 / 2,400 | - | 37 / 46 / 59 | 8 | 88 | - | - | 3 / 12 | 5 (0) | 20 |
| 25 | before | 4.7 | 0 | 1,600 / 2,400 | 4,400 | 30 / 40 / 62 | 8 | 29 | 1,100 | 89 | 5 / 14 | 4 (0) | 151 |
| 25 | after | 4.4 | 0 | 1,600 / 2,400 | 4,400 | 32 / 70 / 77 | 7 | 10 | 1,100 | 71 | 4 / 16 | 7 (0) | 61 |
| 50 | before | 9.1 | 0 | 1,600 / 2,400 | 4,400 | 29 / 40 / 48 | 8 | 60 | 1,100 | 110 | 7 / 19 | 6 (0) | 306 |
| 50 | after | 9.3 | 0 | 1,600 / 2,500 | 4,400 | 31 / 56 / 72 | 9 | 61 | 1,000 | 76 | 8 / 17 | 9 (1) | 128 |
| 100 | before | 18.3 | 1 | 1,600 / 2,400 | 4,400 | 30 / 46 / 71 | 10 | 70 | 1,000 | 100 | 14 / 23 | 10 (1) | 576 |
| 100 | after | 17.7 | 0 | 1,600 / 2,400 | 4,500 | 29 / 61 / 76 | 14 | 66 | 1,000 | 74 | 13 / 28 | 17 (1) | 242 |
| 200 | before | 33.0 | 0 | 1,600 / 2,400 | 4,400 | 30 / 66 / 120 | 19 | 81 | 1,000 | 100 | 30 / 52 | 11 (1) | 1,139 |
| 200 | after | 33.1 | 0 | 1,600 / 2,500 | 4,400 | 29 / 72 / 110 | 11 | 79 | 1,000 | 94 | 28 / 62 | 20 (2) | 494 |
| 400 | before | 61.7 | 15 | 1,700 / 2,700 | 4,900 | 54 / 420 / 870 | 220 | 430 | 1,300 | 560 | 73 / 123 | 20 (15) | 2,076 |
| 400 | after | 62.0 | 38 | 1,600 / 2,500 | 4,500 | 32 / 130 / 200 | 20 | 120 | 1,000 | 93 | 61 / 87 | 21 (4) | 909 |
| 800 | after | 100.2 | 0 | 2,500 / 4,400 | 6,800 | 420 / 1,400 / 1,700 | 520 | 970 | 2,200 | 790 | 151 / 241 | 41 (31) | 1,319 |

Reports, before: [10](../../loadtests/results/2026-11-12-1855-before-10u.md),
[25](../../loadtests/results/2026-11-12-1858-before-25u.md),
[50](../../loadtests/results/2026-11-12-1902-before-50u.md),
[100](../../loadtests/results/2026-11-12-1905-before-100u.md),
[200](../../loadtests/results/2026-11-12-1910-before-200u.md),
[400](../../loadtests/results/2026-11-12-1914-before-400u.md). After:
[10](../../loadtests/results/2026-11-12-1933-after-10u.md),
[25](../../loadtests/results/2026-11-12-1937-after-25u.md),
[50](../../loadtests/results/2026-11-12-1940-after-50u.md),
[100](../../loadtests/results/2026-11-12-1944-after-100u.md),
[200](../../loadtests/results/2026-11-12-1947-after-200u.md),
[400](../../loadtests/results/2026-11-12-1950-after-400u.md),
[800](../../loadtests/results/2026-11-12-1959-after-800u.md). Each report has every endpoint,
the answers' time to sources and completion, the calls to the stub, and the resource use per
container, the top Postgres statements, Qdrant and the API's own time per route.

**About the failures.** The one failure at 100 users before tuning was a reader opening a
document the uploader had just deleted: a correct 404, now avoided by the harness. The 15 and 38
failures at 400 users were conversation deletes with the previous run's revoked tokens, sent by
Locust worker processes of the 200-user run that had outlived it and reported to the next run's
master. The harness now stops Locust's whole process group after a run. Neither was a failure of
the stack.

**Throughput** grows with users because each simulated person waits 3 to 10 s between actions;
req/s is a consequence of the user count, not a capacity limit. Chat questions per second were
about 0.3 at 25 users, 1.2 at 100, 4.2 at 400 and 6.2 at 800.

## What limited the stack before tuning

**10 to 200 users: nothing.** Latencies did not change with load. Chat time to first token p50
1.6 s, of which at least 0.62 s (fast path) or 1.24 s plus the planner's output time (planned) is
the stub's configured model time. On average the API used 3 to 30% of one core, Postgres 1 to
5%, Qdrant, Valkey, OpenFGA and Caddy at most 2% each. Postgres was idle in practice: the
statement with the most total time at 200 users (the audit log's lock, below) used 0.4 s in 3
minutes. Qdrant answered 95% of queries in under 1 ms at 100 users and under 5 ms at 200 (its
histogram's buckets); over all 31,752 queries since it started, the 800-user step included, its
mean was 1.75 ms. The embedding server saw a few queries per second against a capacity of about
500.

**400 users: the API's one process.** The API container used 73% of a core on average and up
to 123% (the event loop plus a few threads). The database pool (10 + 10) was full: 20
connections, 15 of them idle in a transaction, held by requests waiting for the busy event
loop to resume them. Every endpoint's tail grew: search p95 420 ms (46 ms at 100 users),
conversation list p95 220 ms (10 ms), sign-in p95 560 ms. Postgres, Qdrant, Valkey, OpenFGA and
Keycloak stayed under 15% of a core each on average.

A py-spy profile of the API process during a separate 400-user run (45 s at 50 Hz, blocking
mode; shares of the event loop thread, which was idle 10% of the time):

| Where the event loop's time went (400 users, before) | Share |
|---|---:|
| Writing run events to Valkey (`RunEvents.emit`: XADD then EXPIRE, two round trips per event, one event per streamed word) | 14.8% |
| Event streams to browsers (`XREAD` per event 9.4%, `is_disconnected()` per event 2.2%) | 12.0% |
| Access-token authentication (tenant, token, user, departments, groups: 2 transactions, 5 queries) | 7.8% |
| Retrieval (embedding the query, 2 Qdrant queries, OpenFGA batch check, review dates) | 5.8% |
| Parsing the model's streamed response (httpx, JSON per chunk) | 5.7% |
| All Valkey commands / all SQLAlchemy execution / all outgoing HTTP | 22.1% / 8.4% / 11.1% |

The stub sends one word per chunk. Real providers send a few tokens per chunk, so with them the
per-event cost per answer is lower than here; it is still paid per chunk.

## What was changed

| Change | Where | Evidence it addresses |
|---|---|---|
| Streamed text is sent as `token` events of at most one per 50 ms; the first piece goes out at once, so time to first token is unchanged, and a timer sends text that would otherwise wait | `TokenBatcher` in `modules/conversations/events.py`, used by `runner.py` | 27% of the event loop in the per-event path |
| `RunEvents.emit` sends XADD and EXPIRE in one pipeline: one round trip | `events.py` | two Valkey round trips per event |
| The event stream no longer calls `request.is_disconnected()` per event: Starlette's `StreamingResponse` already listens for the disconnect and cancels the generator (uvicorn reports ASGI 2.3) | `api/v1/conversations.py` | 2.2% of the event loop |
| Two API processes by default: `API_WORKERS` (compose, uvicorn's `WEB_CONCURRENCY`), `api.workers` (Helm). The code already ran in several processes (Celery): run events, in-flight slots and rate limits are in Valkey; caches re-check the database within seconds; the authorization outbox locks its rows. One piece of per-process state was missed and fixed after a review: the analytics query pool counted each person's running queries per process (so one person could run two at once) and gave each process `ANALYTICS_WORKERS` workers (doubling the container's memory bound); the per-person count is now in Valkey and the workers are split across the processes | `compose.yaml`, the Helm chart | one core was the API's ceiling |
| `DB_POOL_SIZE` and `DB_MAX_OVERFLOW` (default 10 and 10, as before), so more processes can stay under Postgres's `max_connections` | `core/config.py`, `core/db.py` | the pool was full at 400 users |

Each change has tests: the batcher's timing, order and error handling; a fast stream arriving as
fewer events with the same text; a disconnect ending the event stream while the answer is still
stored; the stream's expiry still set; the pool settings reaching the engine; the Helm chart
refusing `api.workers: 0`.

No cache was added. The harness got a resource monitor and three fixes (readers skip the upload
collection; Locust's worker processes are stopped after a run; Qdrant's mean latency is read from
its histogram sum, so the Qdrant mean column of the reports made before that fix shows a dash).

## After tuning

**Up to 200 users** latencies are the same as before: at these loads WorkMind did not limit them. Valkey
commands per second fell by 52 to 60% at every step. The API container's CPU stayed about the
same while being spread over two processes, and its memory doubled (about 250 MB per process).

Search p95 at 25 to 100 users came out 15 to 30 ms higher after the change (p50 unchanged at
about 30 ms); the API's own time shows the same. It is not the second process: the tuned code
with one process gave search p95 99 ms at 50 users
([report](../../loadtests/results/2026-11-12-2004-batched-1proc-50u.md)), higher than both. With
50 to 200 searches per run, p95 rests on the slowest handful; on this shared laptop it varied
between 40 and 99 ms from run to run, and differences in that range are not results.

**At 400 users** chat time to first token p95 is 2.5 s, the same as with 10 users; search p95
130 ms instead of 420 ms, p99 200 ms instead of 870 ms; conversation list p95 20 ms instead of
220 ms; sign-in p95 93 ms instead of 560 ms; connections idle in a transaction 4 instead of 15.

Which change did it: the tuned code was also run with one API process (`API_WORKERS=1`,
[report](../../loadtests/results/2026-11-12-2007-batched-1proc-400u.md)).

| 400 users | Chat first token p95 | Agents first token p95 | Search p50 / p95 / p99 | Conversations list p95 | Sign-in p95 | API CPU mean / peak | DB connections | Valkey cmd/s |
|---|---:|---:|---|---:|---:|---|---|---:|
| Before: one process | 2,700 | 4,900 | 54 / 420 / 870 | 220 | 560 | 73 / 123 | 20 (15) | 2,076 |
| Batched events, one process | 2,600 | 5,000 | 39 / 240 / 430 | 32 | 110 | 59 / 126 | 12 (3) | 890 |
| Batched events, two processes | 2,500 | 4,500 | 32 / 130 / 200 | 20 | 93 | 61 / 87 | 21 (4) | 909 |

The event changes alone took a fifth off the API's CPU and ended the pool exhaustion; most of the
tail went with them. The second process halved search's tail again and lowered the API
container's CPU peak from 126% to 87% of a core.

Profiles of the two processes in a separate 400-user run: the event loops were idle 24 and 25% of
the time (10% before, with one process); the per-event path took 11 to 13% of each loop (27%
before); authentication 8 to 9%, parsing the model's stream 9%, retrieval 6%.

## What limits it now

At **800 users** (100 requests per second, 6.2 chat questions per second, up to 89 model calls at
once) the tuned stack degrades, without failures:

- **The API processes.** The container used 151% of a core on average and 241% at peak: both
  processes saturated. Chat time to first token p50 2.5 s, p95 4.4 s; search p95 1.4 s.
- **Database connections.** 41 of the 2 x 20 the pools allow, 31 of them idle in a transaction.
- **The audit log's lock.** Every audited action (an answer, a sign-in, an insights query, an
  upload) appends to its tenant's hash chain under a per-tenant advisory lock held until the
  transaction commits. Its wait grew with load and with parallel processes: mean 0.14 ms at 100
  users with one process, 0.33 ms with two; 1.5 ms and 2.0 ms at 400 users; 24 ms at 800 users,
  1 s at worst, with 7 backends waiting at once. A busy event loop holds the lock longer, which
  makes the others wait longer. It is the first limit that more API processes do not remove.
- **Usage reports grow with usage.** The usage summary reads every usage event of the period:
  after the test runs, 19,748 events took 11 ms per query when idle (a sequential scan, about
  0.55 microseconds per event) and the summary runs three such queries; 11 to 17 ms per query
  under load. At 1,000 employees asking 20 questions a day with about 3 model calls each, a
  30-day period holds about 1.8 million events: about a second per query by this rate, three
  seconds for the Analytics page.

## Not changed, and why

- **No cache of identities or access tokens.** Authentication costs about 8% of the event loop,
  but every request reads the user, departments and groups so that a disabled account, a changed
  role or a revoked token applies at once (`api/deps.py`, `identity/tokens.py`). A cache would
  trade that away.
- **No query-embedding cache.** The embedding server answered in about 7 ms with capacity to
  spare. In `auto` mode a question is embedded twice (the fast path, then retrieval), which costs
  one extra 7 ms call; not worth a cache that must be keyed by tenant and permissions.
- **The audit chain.** One chain per tenant is what makes the log tamper-evident
  ([governance](../governance.md)). Removing the lock means a different chain design (for
  example per-day or batched appends), a decision of its own.
- **Usage rollups.** A daily rollup table would make the usage reports independent of volume; not
  built yet.
- **`pool_pre_ping`.** It costs a round trip per checkout, 0.2% of the event loop; it keeps a
  restarted Postgres from failing the first requests.

## Recommended sizing (extrapolation)

**These are extrapolations, not measurements.** They assume that at the busiest moment about 20%
of employees use WorkMind at the same time, at the intensity of the simulated users (an action
every 3 to 10 s, more than a person keeps up for long). They are based on the measured steps
above on one laptop, with the demo's small corpus and one tenant, and with a stub instead of a
model. A real model's latency, throughput and rate limits are not included and usually decide
the experience.

Measured, all WorkMind containers together (not the embedding server, not Locust): 0.2 cores on
average at 100 users, 0.4 at 200, 1.0 at 400 (2.2 if every container peaked at once) and 2.5 at
800; memory 3.7 GiB at 100 users and 4.4 GiB at 800, most of it fixed (Qdrant 1.3 GB at this
corpus size, Keycloak 0.8 GB, the API about 250 to 400 MB per process, Postgres 0.2 to 0.4 GB,
the Celery worker 0.4 GB).

| Employees | Busy moment (assumed) | Measured nearest | API | Machine for the stack (extrapolated) | Database |
|---|---|---|---|---|---|
| 50 | 10 users | 10 to 25 users: the stack used about 0.1 core; nothing near a limit | 1 or 2 processes | 2 to 4 vCPU, 8 GB | defaults |
| 200 | 40 users | 50 users: the stack used about 0.15 core, the API 8% of one | 2 processes (the default) | 4 vCPU, 8 GB | defaults |
| 1,000 | 200 users | 200 users: 0.4 core on average, latencies at their unloaded values; 400 users (twice the assumption) with 2 processes: chat latencies unloaded, 1 core | 2 processes, 3 to 4 for headroom (or 2 replicas) | 4 to 8 vCPU, 16 GB | `max_connections` above processes x (`DB_POOL_SIZE` + `DB_MAX_OVERFLOW`) plus Keycloak's and OpenFGA's (OpenFGA held 30); usage rollups once a month of usage events passes about a million rows |

Add the embedding server. Here it ran on the laptop's GPU (Metal); the compose `embeddings`
profile runs the same model on CPU, which was not measured and is slower: give it its own cores,
or use a GPU, for 1,000 employees. Corpus growth (more chunks in Qdrant's memory, a larger
Postgres) and a second tenant are not in these numbers.

**The model provider.** Peak model calls in flight with the stub: 13 at 100 users, 27 at 200,
38 at 400 and 89 at 800. A call to a real model lasts longer than the stub's 0.6 to 3 s, and the
number in flight grows in proportion: with answers that take three times as long, expect about
three times as many concurrent calls. Check the provider's requests and tokens per minute
before sizing anything else.

**Not measured:** a real model; a corpus larger than 421 chunks (Qdrant and Postgres will grow);
more than one tenant; more than 9 distinct people; ingestion beyond 2 KB uploads at under one per
second; the web app's pages (Next.js), MCP, meetings and workflows; Linux servers, Kubernetes and
the Helm chart under load; runs longer than 3 minutes.

## Reproducing

```bash
make loadtest-stub                                             # the stub on 127.0.0.1:8490
ASSISTANT_MODE=auto docker compose -f compose.yaml -f loadtests/compose.stub.yaml up -d --wait
(cd loadtests && uv run python -m workmind_load.tokens upload-collection)
make loadtest USERS=100 SPAWN_RATE=10 DURATION=3m STUB_URL=http://127.0.0.1:8490 \
  UPLOAD_COLLECTION=loadtest-uploads MONITOR=workmind LABEL=after-100u
ASSISTANT_MODE=auto docker compose up -d --wait                # back to the model in .env
```

For statement statistics, start Postgres with `shared_preload_libraries=pg_stat_statements`
(a compose override for the `postgres` service's `command`) and run
`CREATE EXTENSION pg_stat_statements` in the `postgres` database; the monitor resets and reads it.
For 200 users and more, add `PROCESSES=4`.
