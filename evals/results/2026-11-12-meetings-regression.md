# WorkMind evaluation: 2026-11-12-meetings-regression

- date: `2026-11-12 08:54 UTC`
- commit: `6b021fa`
- tenant: `Acme Robotics (restricted_data: allow)`
- indexed chunks: `351`
- embedding model: `embeddinggemma-300m (768 dims)`
- hybrid fusion: `RRF k=2, BM25 weight 0.5, trust boost on`
- answer model: `claude-sonnet-5-5`
- judge model: `not used`
- assistant: `single (fast path: score >= 0.3, margin >= 0.15)`
- datasets: `82 QA, 16 red team, 6 abstention, 0 cross-department, 0 routing, 0 analytics`

## Permission safety (gate: 0 leaks)

**PASS**: 0 leak(s) across 0 retrieval checks, 0 red-team answers and 0 analytics answers.

## Meetings (extraction)

| Meeting | From | Decisions | Ideas nobody agreed to, reported | Action items (owner and task) | Due dates | Timestamped | Transcribe | Extract |
|---|---|---|---|---|---|---|---|---|
| mt-01 | transcript | 4/4 (+2 more) | 0 | 5/5 | 5/5 | 100.0% | - | 10 s |
