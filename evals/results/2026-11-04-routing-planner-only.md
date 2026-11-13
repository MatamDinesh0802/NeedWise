# WorkMind evaluation: 2026-11-04 routing (planner only)

- date: `2026-11-04 09:11 UTC`
- commit: `5e7e943`
- tenant: `Acme Robotics (restricted_data: allow)`
- indexed chunks: `294`
- embedding model: `embeddinggemma-300m (768 dims)`
- hybrid fusion: `RRF k=2, BM25 weight 0.5, trust boost on`
- answer model: `none (retrieval only)`
- judge model: `not used`
- assistant: `single (fast path off)`
- datasets: `82 QA, 16 red team, 6 abstention, 0 cross-department, 33 routing`

## Permission safety (gate: 0 leaks)

**PASS**: 0 leak(s) across 0 retrieval checks and 0 red-team answers.

## Routing (planner and fast path)

Exact agent set: **32/33**. Fast path used for 0 questions, 0 of them correct. Planner latency p50 2.9 s.

| Expected | Questions | Correct |
|---|---|---|
| single | 18 | 17/18 |
| fan_out | 12 | 12/12 |
| direct | 3 | 3/3 |
- ro-s13: expected ['support'], got ['engineering', 'support'] (planner)
