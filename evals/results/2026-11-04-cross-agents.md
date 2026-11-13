# WorkMind evaluation: 2026-11-04-cross-agents

- date: `2026-11-04 09:23 UTC`
- commit: `5e7e943`
- tenant: `Acme Robotics (restricted_data: allow)`
- indexed chunks: `294`
- embedding model: `embeddinggemma-300m (768 dims)`
- hybrid fusion: `RRF k=2, BM25 weight 0.5, trust boost on`
- answer model: `claude-opus-5-5`
- judge model: `claude-opus-5-5`
- assistant: `agents (fast path: score >= 0.3, margin >= 0.15)`
- datasets: `82 QA, 16 red team, 6 abstention, 15 cross-department, 0 routing`
- rescored: `2026-11-04 09:24 UTC`

## Permission safety (gate: 0 leaks)

**PASS**: 0 leak(s) across 0 retrieval checks and 0 red-team answers.

## Cross-department questions

| Metric | Value |
|---|---|
| Every required fact | 15/15 |
| Cites every expected document | 15/15 |
| Invalid citation markers | 0/15 |
| Complete answer, p50 / p95 | 19.2 s / 27.9 s |
| Model calls per answer (mean) | 5.1 |
| Cost per answer (mean) | $0.0978 |
| Faithfulness (judge, mean) | 100.0% |

## Latency and cost (answers, including retrieval)

| Metric | p50 | p95 |
|---|---|---|
| Time to first token | 15.1 s | 21.6 s |
| Complete answer | 19.2 s | 27.9 s |

Mean tokens per answer: 5069 in / 805 out. Mean cost per answer: $0.0978.
