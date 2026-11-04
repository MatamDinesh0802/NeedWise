# WorkMind evaluation: 2026-11-04-cross-auto

- date: `2026-11-04 09:28 UTC`
- commit: `57c9802`
- tenant: `Acme Robotics (restricted_data: allow)`
- indexed chunks: `294`
- embedding model: `embeddinggemma-300m (768 dims)`
- hybrid fusion: `RRF k=2, BM25 weight 0.5, trust boost on`
- answer model: `claude-opus-5-5`
- judge model: `claude-opus-5-5`
- assistant: `auto (fast path: score >= 0.3, margin >= 0.15)`
- datasets: `82 QA, 16 red team, 6 abstention, 15 cross-department, 0 routing`

## Permission safety (gate: 0 leaks)

**PASS**: 0 leak(s) across 0 retrieval checks and 0 red-team answers.

## Cross-department questions

| Metric | Value |
|---|---|
| Every required fact | 15/15 |
| Cites every expected document | 15/15 |
| Invalid citation markers | 0/15 |
| Complete answer, p50 / p95 | 22.3 s / 24.7 s |
| Model calls per answer (mean) | 5.0 |
| Cost per answer (mean) | $0.0952 |
| Faithfulness (judge, mean) | 100.0% |

## Latency and cost (answers, including retrieval)

| Metric | p50 | p95 |
|---|---|---|
| Time to first token | 17.4 s | 19.4 s |
| Complete answer | 22.3 s | 24.7 s |

Mean tokens per answer: 5089 in / 748 out. Mean cost per answer: $0.0952.
