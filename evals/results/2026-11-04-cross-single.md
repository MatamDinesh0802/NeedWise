# WorkMind evaluation: 2026-11-04-cross-single

- date: `2026-11-04 09:21 UTC`
- commit: `57c9802`
- tenant: `Acme Robotics (restricted_data: allow)`
- indexed chunks: `294`
- embedding model: `embeddinggemma-300m (768 dims)`
- hybrid fusion: `RRF k=2, BM25 weight 0.5, trust boost on`
- answer model: `claude-opus-5-5`
- judge model: `claude-opus-5-5`
- assistant: `single (fast path: score >= 0.3, margin >= 0.15)`
- datasets: `82 QA, 16 red team, 6 abstention, 15 cross-department, 0 routing`
- rescored: `2026-11-04 09:24 UTC`

## Permission safety (gate: 0 leaks)

**PASS**: 0 leak(s) across 0 retrieval checks and 0 red-team answers.

## Cross-department questions

| Metric | Value |
|---|---|
| Every required fact | 14/15 |
| Cites every expected document | 14/15 |
| Invalid citation markers | 0/15 |
| Complete answer, p50 / p95 | 6.6 s / 7.8 s |
| Model calls per answer (mean) | 1.0 |
| Cost per answer (mean) | $0.0216 |
| Faithfulness (judge, mean) | 98.7% |

- xd-14 (bob): missing ["['1 hour', 'one hour']", '1.5']; cited ['oncall-runbook.md', 'support-sla.md']; agents -

## Latency and cost (answers, including retrieval)

| Metric | p50 | p95 |
|---|---|---|
| Time to first token | 3.8 s | 5.1 s |
| Complete answer | 6.6 s | 7.8 s |

Mean tokens per answer: 2624 in / 555 out. Mean cost per answer: $0.0216.
