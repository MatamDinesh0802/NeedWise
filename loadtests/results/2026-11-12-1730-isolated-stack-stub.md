# Load test: isolated-stack-stub

| | |
|---|---|
| Date (UTC) | 2026-11-12 17:30 |
| Target | http://localhost:18480 (WorkMind 0.0.1, dev) |
| Harness checkout | commit 7e274f5 (the target may run other code) |
| Users, spawn rate, duration | 15 users, 3.0/s, 2m |
| Mix (class weights) | reader=3, poller=2, chat=3, agents=2, analyst=2, admin=1, login=1, upload=1 |
| Model | stub at http://127.0.0.1:8491 (latency 20 ms, first token after 600 ms, 60 words/s, 120-word answers) |
| Load generator | Locust 2.47.0, one process, Darwin arm64, 10 CPUs |
| Source | `locust.json` |

## Summary

- 464 HTTP requests, 0 failed (0.00%), 3.80 requests/s over 122 s.
- Slowest at p95: insights: ask (3,000 ms), insights: data model (2,000 ms), login: callback (1,800 ms).
- agents: time to first token: p50 1,800 ms, p95 5,200 ms, p99 5,200 ms over 20 answers.
- chat: time to first token: p50 1,700 ms, p95 3,300 ms, p99 4,000 ms over 33 answers.
- research: time to first token: p50 1,900 ms, p95 2,600 ms, p99 2,600 ms over 3 answers.

## Requests

| Type | Name | Requests | Failures | req/s | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| GET | admin: agents | 2 | 0 | 0.02 | 17 | 17 | 17 | 17 |
| GET | admin: audit log | 4 | 0 | 0.03 | 25 | 300 | 300 | 295 |
| GET | admin: collections | 3 | 0 | 0.02 | 18 | 20 | 20 | 20 |
| GET | admin: models | 1 | 0 | 0.01 | 38 | 38 | 38 | 38 |
| GET | admin: people | 1 | 0 | 0.01 | 18 | 18 | 18 | 18 |
| GET | admin: usage by department | 5 | 0 | 0.04 | 50 | 68 | 68 | 68 |
| GET | agents: list | 4 | 0 | 0.03 | 39 | 82 | 82 | 82 |
| GET | agents: open stream | 20 | 0 | 0.16 | 13 | 400 | 400 | 400 |
| POST | agents: send question | 20 | 0 | 0.16 | 18 | 360 | 360 | 357 |
| GET | approvals: pending | 8 | 0 | 0.07 | 15 | 130 | 130 | 125 |
| GET | chat: open stream | 33 | 0 | 0.27 | 16 | 350 | 550 | 548 |
| POST | chat: send question | 33 | 0 | 0.27 | 23 | 420 | 760 | 756 |
| GET | collections: list | 8 | 0 | 0.07 | 33 | 1,600 | 1,600 | 1,624 |
| POST | conversations: create | 49 | 0 | 0.40 | 19 | 440 | 1,600 | 1,609 |
| DELETE | conversations: delete | 49 | 0 | 0.40 | 9 | 34 | 38 | 38 |
| GET | conversations: list | 21 | 0 | 0.17 | 16 | 92 | 190 | 186 |
| GET | conversations: open | 56 | 0 | 0.46 | 20 | 230 | 610 | 610 |
| GET | documents: list | 6 | 0 | 0.05 | 23 | 43 | 43 | 43 |
| GET | documents: open | 1 | 0 | 0.01 | 17 | 17 | 17 | 17 |
| POST | insights: ask | 4 | 0 | 0.03 | 3,000 | 3,000 | 3,000 | 2,975 |
| GET | insights: data model | 13 | 0 | 0.11 | 25 | 2,000 | 2,000 | 2,048 |
| GET | insights: past answers | 8 | 0 | 0.07 | 34 | 66 | 66 | 66 |
| POST | insights: re-run query | 10 | 0 | 0.08 | 120 | 1,700 | 1,700 | 1,724 |
| GET | login: callback | 9 | 0 | 0.07 | 85 | 1,800 | 1,800 | 1,783 |
| GET | login: keycloak page | 9 | 0 | 0.07 | 130 | 290 | 290 | 292 |
| GET | login: start | 9 | 0 | 0.07 | 10 | 140 | 140 | 140 |
| POST | login: submit email | 9 | 0 | 0.07 | 49 | 390 | 390 | 393 |
| POST | login: submit password | 9 | 0 | 0.07 | 120 | 530 | 530 | 530 |
| POST | logout | 9 | 0 | 0.07 | 23 | 140 | 140 | 142 |
| GET | me | 1 | 0 | 0.01 | 13 | 13 | 13 | 13 |
| GET | me (session) | 7 | 0 | 0.06 | 32 | 630 | 630 | 630 |
| GET | notifications: poll | 8 | 0 | 0.07 | 34 | 120 | 120 | 121 |
| GET | research: open stream | 3 | 0 | 0.02 | 14 | 21 | 21 | 21 |
| POST | research: send question | 3 | 0 | 0.02 | 21 | 31 | 31 | 31 |
| POST | search | 9 | 0 | 0.07 | 39 | 1,800 | 1,800 | 1,801 |
| DELETE | upload: delete | 6 | 0 | 0.05 | 29 | 69 | 69 | 69 |
| GET | upload: poll status | 6 | 0 | 0.05 | 27 | 89 | 89 | 89 |
| POST | upload: send | 6 | 0 | 0.05 | 30 | 1,100 | 1,100 | 1,115 |
| GET | usage: summary | 2 | 0 | 0.02 | 93 | 93 | 93 | 93 |
|  | **All HTTP requests** | 464 | 0 | 3.80 | 21 | 460 | 2,000 | 2,975 |

## What people waited for

Custom metrics (users.py), timed from the person's action: `SSE` from sending the
question to the first `retrieval` event (time to sources), the first `token` event
(time to first token) and `run.completed` (answer complete); `INGEST` from the upload
until the document is `ready`; `FLOW` a whole Keycloak sign-in. A failed one is timed
until it failed.

| Type | Name | Requests | Failures | req/s | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| SSE | agents: answer complete | 20 | 0 | 0.16 | 3,800 | 7,400 | 7,400 | 7,369 |
| SSE | agents: time to first token | 20 | 0 | 0.16 | 1,800 | 5,200 | 5,200 | 5,187 |
| SSE | agents: time to sources | 20 | 0 | 0.16 | 1,200 | 4,600 | 4,600 | 4,557 |
| SSE | chat: answer complete | 33 | 0 | 0.27 | 3,700 | 5,800 | 6,000 | 5,961 |
| SSE | chat: time to first token | 33 | 0 | 0.27 | 1,700 | 3,300 | 4,000 | 3,990 |
| SSE | chat: time to sources | 33 | 0 | 0.27 | 1,000 | 2,700 | 3,000 | 3,017 |
| FLOW | login: whole flow | 7 | 0 | 0.06 | 450 | 2,900 | 2,900 | 2,899 |
| SSE | research: answer complete | 3 | 0 | 0.02 | 1,900 | 2,600 | 2,600 | 2,597 |
| SSE | research: time to first token | 3 | 0 | 0.02 | 1,900 | 2,600 | 2,600 | 2,561 |
| INGEST | upload: time to ready | 6 | 0 | 0.05 | 1,100 | 2,200 | 2,200 | 2,207 |

## Failures

None.

## Calls to the stub model

| Kind | Calls |
|---|---:|
| complete | 7 |
| stream | 53 |
| structured (Composed 4, EvidenceOut 8, PlanOut 53) | 65 |
| tools | 14 |

Peak concurrent model calls since the stub started: 6.

## Target before the run

```text
WorkMind AI 0.0.1 (dev): ok
  postgres: ok
  valkey: ok
  qdrant: ok
  openfga: ok
  keycloak: ok
agents enabled: engineering, finance, hr, leadership, marketing, operations, sales, support
deployment provider: openai_compatible http://host.docker.internal:8491/v1
role agent: system:workmind-stub
role answer: system:workmind-stub
role generator: system:workmind-stub
role judge: system:workmind-stub
role router: system:workmind-stub
data policy local model: None
```

## Notes

- Validation of the harness with every scenario, not a performance result: a throwaway stack (its own compose project, Community edition, hash embeddings instead of the embedding model, ASSISTANT_MODE=auto with the embedding fast path off) on the same laptop as Locust. Every model call went to the stub on the host (loadtests/compose.stub.yaml).
- In auto mode every chat question gets a planner call first (the PlanOut calls above); questions the planner splits over two areas then run department agents (EvidenceOut) and a composed answer. Follow-up questions add a rewrite call (complete).
- The upload user uploaded to a restricted collection made for it (`tokens upload-collection`) and deleted each document once it was ready. Conversations were deleted by the users when they stopped; tokens were revoked after the run; a sweep afterwards found nothing left.
