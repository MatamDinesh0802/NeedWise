# Evaluation

WorkMind's quality and safety claims are measured here, not asserted. The harness arrives in
Phase 3. The plan is in [ARCHITECTURE.md §22](../docs/ARCHITECTURE.md).

```text
evals/
  datasets/   retrieval QA, routing, red team, injection, abstention, text-to-SQL, meetings (JSONL)
  runners/    pytest-based runners, invoked by `wm eval`
  results/    <date>-<git-sha>.json + Markdown report for every release
```

**Merge gate:** the unauthorized-retrieval rate on the red-team set must be **0**.
