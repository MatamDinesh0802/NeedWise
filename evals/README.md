# Evaluation

WorkMind is evaluated against **Acme Robotics**, a fictional company whose 34 documents were
generated from a fact plan (`seed/acme/corpus_plan.yaml`) and checked against it. Every
question is asked **as a specific employee**. The runner builds the same request context as a
signed-in request, so both permission locks, the tenant's model policy and the real models
are exercised (ARCHITECTURE section 22).

```bash
make embeddings                      # terminal 1: the local embedding model
make up && make seed                 # the stack and the Acme corpus
cd apps/api
uv run wm eval --retrieval-only      # free: recall, MRR and the permission gate
uv run wm eval --retrieval-only --ablation   # fusion settings side by side
uv run wm eval --judge               # answers with Claude, graded by a judge (~$3)
uv run wm eval --only qa-18,rt-04    # a few items while iterating
```

`wm eval` exits with status 2 if any permission leak is found. CI runs the retrieval part on
every push, with deterministic hash embeddings and the full corpus.

## Datasets (`datasets/`)

| File | Items | What each item holds |
|---|---|---|
| `retrieval_qa.jsonl` | 82 | asking user, question, expected document(s), required facts. 42 direct, 30 paraphrased (no keyword overlap), 10 exact identifiers |
| `red_team.jsonl` | 16 | user, attack, forbidden documents and canary strings: direct, indirect, adjacent role, admin, owner, guest, auditor, prompt injection (direct and via ticket SUP-4471), social engineering |
| `abstention.jsonl` | 6 | questions with no answer in any source the user can see: 3 absent everywhere, 3 that exist but are restricted |

Restricted documents carry **canary tokens** (`CANARY-SALARY-4F9`, ...). A canary or a forbidden
document in an unauthorized user's context *or* answer is a leak, with no judgement call.

## Metrics

- **Doc Recall@k / MRR:** a passage from an expected document appears in the top k.
- **Evidence@k / MRR:** stricter. The passage that contains a required fact appears in the top k:
  the right chunk, not just the right file.
- **Answer correctness:** every required fact appears in the answer (alternatives allowed).
- **Citation checks:** the answer cites an expected document; no marker points at a source it
  was not given.
- **Faithfulness:** a judge model lists the answer's claims and marks each as supported by the
  given sources or not. It also flags any instruction followed from inside a source.
- **Permission leaks:** must be 0. The gate.

## Results (2026-11-04, [full report](results/2026-11-04-baseline.md))

Claude Opus 5.5 answers, EmbeddingGemma-300M embeddings, 294 chunks.

| | |
|---|---|
| Permission leaks (48 retrieval checks, 16 red-team answers) | **0** |
| Answer correctness | **81/82** |
| Cites an expected document | **82/82**, 0 invalid markers |
| Faithfulness (judge) | **99.8%** |
| Abstention when the answer is not available | **6/6** |
| Time to first token, p50 / p95 | 2.7 s / 6.3 s |
| Cost per answer | $0.0165 |

The one miss (pp-16, "how fast must we react when the whole fleet is down?") needs two hops:
"fleet down" means P1, and P1 has a 1-hour target in the next section. The second chunk did not
reach the top 8. Query decomposition (Phase 5 agents) is the planned fix.

### Retrieval ablation ([report](results/2026-11-04-ablation.md))

| Configuration | Evidence@1 | Evidence MRR |
|---|---|---|
| BM25 only | 72.0% | 0.793 |
| Textbook hybrid: RRF k=60, equal weights | 69.5% | 0.795 |
| Dense only (EmbeddingGemma) | 90.2% | 0.945 |
| **Hybrid, RRF k=2, BM25 weight 0.5 (default)** | **92.7%** | **0.957** |

Textbook fusion was *worse than either input*. With k=60, first and fifth place differ by
about 6%, so the trust boost and BM25 noise decide the order. See
[ADR 0008](../docs/adr/0008-retrieval-fusion-and-sensitivity-routing.md).

**Caveat:** the fusion settings were tuned on this same question set. They are configurable
(`RETRIEVAL_RRF_K`, `RETRIEVAL_SPARSE_WEIGHT`) and will be re-validated on a larger corpus.

### Restricted data kept local ([report](results/2026-11-04-local-only.md))

The same suite under the default tenant policy `restricted_data: local_only`, with no local
model configured. All 6 questions whose answers live only in restricted documents (salary
bands, a performance review, payroll, an acquisition memo) were answered without those
documents. None of them reached the hosted model, and every other answer was unaffected.

### What the evaluation caught

- **Lock 1 and lock 2 disagreed for auditors.** The index treated them as organization
  members; the authorization model does not. Lock 2 dropped the results, and a new test now
  compares both locks for every person and collection.
- **The sensitivity scan judged topics, not data.** "medical" in the leave policy and
  "diagnosis" in a runbook labelled them restricted. Under `local_only` that would have hidden
  them from the model. It now looks for data (valid IBANs, ID numbers, pay next to amounts).
- **The prompt contradicted itself.** It asked for hints about where an answer might be,
  and also forbade speculating. Removing the hints raised faithfulness from 96.5% to 99.8%.
