# 0008. Retrieval: tuned rank fusion, collection-level recheck, routing by sensitivity

- Status: Accepted
- Date: 2026-11-04
- Context documents: ARCHITECTURE sections 7.3, 11, 22; ADR 0006 (two locks); `evals/README.md`

## Context
Phase 3 needed concrete choices for hybrid fusion, for what the OpenFGA recheck (lock 2) checks,
and for what happens to `restricted` documents when the answer model is a hosted API. We decided
them by measurement where we could: `nw eval` runs every question *as its user* against the
real stack.

## Decisions

### 1. Weighted reciprocal rank fusion with k=2 and a BM25 weight of 0.5
Fusion runs in our code (two filtered Qdrant queries, then RRF), not in Qdrant, so it can be
tuned and ablated. On the Acme set (82 questions, evidence-level: the chunk that holds the fact):

| Configuration | Evidence@1 | Evidence MRR |
|---|---|---|
| BM25 only | 72.0% | 0.793 |
| Textbook hybrid: RRF k=60, equal weights, trust boost | 69.5% | 0.795 |
| Dense only (EmbeddingGemma-300M) | 90.2% | 0.945 |
| **Chosen: RRF k=2, BM25 weight 0.5, trust boost** | **92.7%** | **0.957** |

With k=60, rank 1 and rank 5 differ by about 6%, less than the 15% trust boost and smaller
than BM25 noise. A small k keeps first place meaningful; a lower BM25 weight stops keyword
noise from outvoting a confident semantic match while still rescuing exact identifiers.

**Caveat:** these settings were tuned on the same set they are reported on. They are
settings, not constants (`RETRIEVAL_RRF_K`, `RETRIEVAL_SPARSE_WEIGHT`), and the decision is
revisited when the corpus grows (ARCHITECTURE 22.1 targets about 500 documents; the corpus has
49 as of 2026-11-13, so they have not been revisited).

### 2. Lock 2 checks `viewer` on the collection, not on each document
Documents inherit their collection's access. Checking the collection needs one OpenFGA batch
per query (a handful of collections, not dozens of documents) and never races the document
tuple of a file uploaded seconds ago. Any passage that lock 2 rejects is dropped and logged as
`authz_projection_drift`.

This paid off during evaluation: lock 2 caught a real bug. Lock 1 treated auditors as
organization members, while the authorization model (correctly) does not. A new test now
compares both locks for every person and every collection.

### 3. Restricted documents and external models
Tenant policy `restricted_data` is `local_only` by default and `allow` by choice:
- `allow`: restricted sources may go to the configured model.
- `local_only` with an external model: restricted sources go to `LOCAL_LLM_*` if configured.
  Otherwise they are left out, and the answer says how many were withheld and why.
  The routing happens after both locks, so it never widens access.

The Acme demo tenant uses `allow`, because its data is fictional and the maintainer runs Claude.

### 4. The sensitivity scan looks for data, not topics
Word lists ("medical", "diagnosis", "payroll") labelled a leave policy and an on-call runbook
`restricted`, which under `local_only` would have kept them from the model. The scan now
raises labels only for checksum-valid IBANs, national ID numbers, or a pay word next to an
amount. Personal-data collections are labelled through their defaults. Editors can set a label
by hand (`PATCH /documents/{id}`), and re-indexing keeps it.

## Consequences
- Every retrieval change is measured with `nw eval --retrieval-only --ablation` (no LLM cost).
- CI fails if any red-team attack retrieves a forbidden document or canary in any mode.
- Answers need one more OpenFGA round-trip per question; it is retried on overload (read-only).
