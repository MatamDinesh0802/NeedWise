# WorkMind evaluation: 2026-11-12-routing-legal-rejected-rule

> A planner wording that was tried and not kept: "direct" narrowed to questions that need no company documents, plus a sentence telling the planner to route names it does not recognise to the most likely agent. It fixed ro-s18 but added an agent to ro-s16 and ro-s17. The kept wording is the first sentence alone ([docs/agents.md](../../docs/agents.md#how-routing-works)).

- date: `2026-11-12 14:31 UTC`
- commit: `d855f5c`
- tenant: `Acme Robotics (external models receive up to: restricted)`
- indexed chunks: `408`
- embedding model: `embeddinggemma-300m (768 dims)`
- hybrid fusion: `RRF k=2, BM25 weight 0.5, trust boost on`
- answer model: `system:claude-sonnet-5-5`
- judge model: `not used`
- planner model: `system:claude-sonnet-5-5`
- assistant: `single (fast path: score >= 0.3, margin >= 0.15)`
- datasets: `82 QA, 16 red team, 6 abstention, 0 cross-department, 56 routing, 0 analytics`

## Permission safety (gate: 0 leaks)

**PASS**: 0 leak(s) across 0 retrieval checks, 0 red-team answers and 0 analytics answers.

## Routing (planner and fast path)

Exact agent set: **52/56**. Fast path used for 10 questions, 10 of them correct. Planner latency p50 1.7 s.

| Expected | Questions | Correct |
|---|---|---|
| single | 33 | 29/33 |
| fan_out | 20 | 20/20 |
| direct | 3 | 3/3 |

### By agent

Expected in: questions whose expected plan includes the agent. Exact: of those, the plan had exactly the expected agents. Picked: of those, the plan included the agent. Picked when not expected: false positives over every question that ran.

| Agent | Expected in | Exact | Picked | Picked when not expected |
|---|---|---|---|---|
| engineering | 10 | 10/10 | 10/10 | 2 |
| finance | 14 | 13/14 | 14/14 | 1 |
| hr | 8 | 8/8 | 8/8 | 1 |
| leadership | 2 | 2/2 | 2/2 | 0 |
| legal | 15 | 14/15 | 15/15 | 0 |
| marketing | 4 | 2/4 | 4/4 | 0 |
| operations | 7 | 7/7 | 7/7 | 0 |
| sales | 7 | 7/7 | 7/7 | 0 |
| support | 9 | 9/9 | 9/9 | 0 |

Near misses kept away from the agent they sound like: **3/3**.

### Wrong plans

- ro-s06: expected ['finance'], got ['engineering', 'finance'] (planner)
- ro-s16: expected ['marketing'], got ['finance', 'marketing'] (planner)
- ro-s17: expected ['marketing'], got ['engineering', 'marketing'] (planner)
- ro-lg-07: expected ['legal'], got ['hr', 'legal'] (planner)
