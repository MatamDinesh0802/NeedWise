# Load test: smoke-shared-stack

| | |
|---|---|
| Date (UTC) | 2026-11-12 17:37 |
| Target | http://localhost:8480 (WorkMind 0.0.1, dev) |
| Harness checkout | commit 4b55ca3 (the target may run other code) |
| Users, spawn rate, duration | 5 users, 1.0/s, 60s |
| Mix (class weights) | reader=1, poller=1, analyst=1, admin=1, login=1 |
| Model | none: tasks that call the model were off (--wm-no-model) |
| Load generator | Locust 2.47.0, one process, Darwin arm64, 10 CPUs |
| Source | `locust.json` |

## Summary

- 61 HTTP requests, 0 failed (0.00%), 1.00 requests/s over 61 s.
- Slowest at p95: insights: re-run query (12,000 ms), conversations: open (9,000 ms), login: keycloak page (4,500 ms).

## Requests

| Type | Name | Requests | Failures | req/s | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| GET | admin: audit log | 1 | 0 | 0.02 | 38 | 38 | 38 | 38 |
| GET | admin: collections | 1 | 0 | 0.02 | 30 | 30 | 30 | 30 |
| GET | admin: people | 3 | 0 | 0.05 | 59 | 310 | 310 | 307 |
| GET | admin: usage by department | 4 | 0 | 0.07 | 150 | 3,300 | 3,300 | 3,297 |
| GET | approvals: pending | 2 | 0 | 0.03 | 43 | 43 | 43 | 43 |
| GET | collections: list | 1 | 0 | 0.02 | 47 | 47 | 47 | 47 |
| GET | conversations: list | 4 | 0 | 0.07 | 64 | 76 | 76 | 76 |
| GET | conversations: open | 1 | 0 | 0.02 | 9,000 | 9,000 | 9,000 | 8,977 |
| GET | insights: data model | 2 | 0 | 0.03 | 50 | 50 | 50 | 50 |
| GET | insights: past answers | 2 | 0 | 0.03 | 1,000 | 1,000 | 1,000 | 1,025 |
| POST | insights: re-run query | 3 | 0 | 0.05 | 350 | 12,000 | 12,000 | 12,039 |
| GET | login: callback | 5 | 0 | 0.08 | 150 | 410 | 410 | 410 |
| GET | login: keycloak page | 5 | 0 | 0.08 | 88 | 4,500 | 4,500 | 4,490 |
| GET | login: start | 5 | 0 | 0.08 | 8 | 1,600 | 1,600 | 1,561 |
| POST | login: submit email | 5 | 0 | 0.08 | 31 | 990 | 990 | 988 |
| POST | login: submit password | 5 | 0 | 0.08 | 75 | 2,800 | 2,800 | 2,796 |
| POST | logout | 5 | 0 | 0.08 | 23 | 320 | 320 | 322 |
| GET | me (session) | 4 | 0 | 0.07 | 38 | 60 | 60 | 60 |
| GET | notifications: poll | 2 | 0 | 0.03 | 52 | 52 | 52 | 52 |
| POST | search | 1 | 0 | 0.02 | 1,700 | 1,700 | 1,700 | 1,688 |
|  | **All HTTP requests** | 61 | 0 | 1.00 | 47 | 3,300 | 12,000 | 12,039 |

## What people waited for

Custom metrics (users.py), timed from the person's action: `SSE` from sending the
question to the first `retrieval` event (time to sources), the first `token` event
(time to first token) and `run.completed` (answer complete); `INGEST` from the upload
until the document is `ready`; `FLOW` a whole Keycloak sign-in. A failed one is timed
until it failed.

| Type | Name | Requests | Failures | req/s | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| FLOW | login: whole flow | 4 | 0 | 0.07 | 440 | 10,000 | 10,000 | 10,248 |

## Failures

None.

## Target before the run

```text
WorkMind AI 0.0.1 (dev): ok
  postgres: ok
  valkey: ok
  qdrant: ok
  openfga: ok
  keycloak: ok
agents enabled: engineering, finance, hr, leadership, legal, marketing, operations, sales, support
deployment provider: anthropic
role agent: system:claude-sonnet-5-5
role answer: system:claude-sonnet-5-5
role generator: system:claude-sonnet-5-5
role judge: system:claude-sonnet-5-5
role router: system:claude-sonnet-5-5
data policy local model: None
```

## Notes

- Validation of the harness, not a performance result: 5 users for 60 s on the shared development stack (Docker Desktop on the same laptop as Locust), which other work was using at the same time.
- No model calls: chat, agents, research and insights questions were off (the stack answers with the real Claude model). Search embeds the query with the stack's local embedding server.
- One user per class: reader, poller, analyst, admin, login. Tokens were created for the 9 demo logins before the run and revoked after it.
