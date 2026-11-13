# WorkMind evaluation: 2026-11-13-request-routing

- date: `2026-11-13 05:14 UTC`
- commit: `07fe2ba`
- tenant: `Acme Robotics (external models receive up to: restricted)`
- indexed chunks: `430`
- embedding model: `embeddinggemma-300m (768 dims)`
- hybrid fusion: `RRF k=2, BM25 weight 0.5, trust boost on`
- answer model: `system:claude-sonnet-5-5`
- judge model: `not used`
- assistant: `single (fast path: score >= 0.3, margin >= 0.15)`
- datasets: `82 QA, 68 red team, 6 abstention, 0 cross-department, 0 routing, 0 analytics, 39 requests to route`
- planner model: `system:claude-sonnet-5-5`

## Permission safety (gate: 0 leaks)

**PASS**: 0 leak(s) across 0 retrieval checks, 0 red-team answers and 0 analytics answers.

## Request routing (department queues)

Correct queue: **36/39** (rule items 23/23, model items 8/11, triage items 5/5). Sent to a wrong department: **1**. Sent to triage: 7. Rules behaved as the dataset expects on 39/39. Router model calls that failed: **0**.

| Item | Expected | Routed | By | Confidence |
|---|---|---|---|---|
| rr-01 | finance | finance | rule | 1.00 |
| rr-02 | finance | finance | rule | 1.00 |
| rr-03 | finance | finance | rule | 1.00 |
| rr-04 | finance | finance | rule | 1.00 |
| rr-05 | hr | hr | rule | 1.00 |
| rr-06 | hr | hr | rule | 1.00 |
| rr-07 | hr | hr | rule | 1.00 |
| rr-08 | hr | hr | rule | 1.00 |
| rr-09 | operations | operations | rule | 1.00 |
| rr-10 | operations | operations | rule | 1.00 |
| rr-11 | operations | operations | rule | 1.00 |
| rr-12 | operations | operations | rule | 1.00 |
| rr-13 | operations | operations | rule | 1.00 |
| rr-14 | operations | operations | rule | 1.00 |
| rr-15 | engineering | engineering | rule | 1.00 |
| rr-16 | engineering | engineering | rule | 1.00 |
| rr-17 | support | support | rule | 1.00 |
| rr-18 | support | support | rule | 1.00 |
| rr-19 | sales | sales | rule | 1.00 |
| rr-20 | sales | sales | rule | 1.00 |
| rr-21 | marketing | marketing | rule | 1.00 |
| rr-22 | marketing | marketing | rule | 1.00 |
| rr-23 | marketing | marketing | rule | 1.00 |
| rr-24 | hr | hr | model | 0.93 |
| rr-25 | hr | hr | model | 0.70 |
| rr-26 | finance | management ✗ | model | 0.70 |
| rr-27 | finance | triage ✗ | triage | 0.55 |
| rr-28 | finance | finance | model | 0.72 |
| rr-29 | operations | operations | model | 0.80 |
| rr-30 | engineering | engineering | model | 0.88 |
| rr-31 | engineering | engineering | model | 0.80 |
| rr-32 | support | support | model | 0.80 |
| rr-33 | sales | sales | model | 0.70 |
| rr-34 | marketing | triage ✗ | triage | 0.60 |
| rr-35 | triage | triage | triage | 0.97 |
| rr-36 | triage | triage | triage | 0.10 |
| rr-37 | triage | triage | triage | 0.05 |
| rr-38 | triage | triage | triage | 0.05 |
| rr-39 | triage | triage | triage | 0.10 |
