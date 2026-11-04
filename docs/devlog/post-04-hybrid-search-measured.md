# LinkedIn post #4: Textbook hybrid search made our retrieval worse

- **Status:** Draft, not posted
- **Milestone:** Phase 3 (retrieval engine and evaluation harness)
- **Image:** `assets/post-04-fusion-ablation.png` (1080×1350). Every number comes from `evals/results/2026-11-04-ablation.md` and `2026-11-04-baseline.md`; the chart has a zero baseline.
- **Post only after** the repo is public. Mention the tuning caveat if anyone asks (it is in the post, keep it).

---

## Post

"Use hybrid search" is the standard RAG advice.

I measured it. The textbook version made my retrieval worse.

The setup: a fictional company with 34 documents, 82 questions, and each question asked as a specific employee, with that person's permissions. The metric is strict: is the very first passage the one that actually holds the answer?

→ Keyword search (BM25) alone: 72.0%
→ Vector search alone: 90.2%
→ Textbook hybrid (reciprocal rank fusion, k=60, equal weights): 69.5%
→ Tuned hybrid (k=2, keyword weight 0.5): 92.7%

Why the textbook version lost: with k=60, first and fifth place score within about 6% of each other. A small "official document" boost and keyword noise then decide the order, not relevance. A smaller k keeps first place meaningful, and a lower keyword weight stops noise from outvoting a confident semantic match. Exact codes like "INC-2026-031" still get rescued.

Honest caveat: I tuned on the same 82 questions I report on. The settings are configurable, and I'll re-check them on a bigger corpus.

The number I care about more: zero.

16 attacks: an engineer asking for salaries, an admin asking for an acquisition memo, a contractor asking for financials, "ignore previous instructions" prompts, and a support ticket with an injection hidden inside. Restricted documents contain canary tokens. If a canary appears in what the model sees or says, that's a leak, no judgement needed.

Result: 0 leaks across every attack and every search mode. CI now blocks any change that breaks that.

The evaluation also caught a real bug. The second permission check (OpenFGA) refused results the first check had allowed. Auditors were treated as company members in one place but not the other. The second lock did its job; a test now proves both locks agree for every person.

End to end with Claude: 81 of 82 answers contain every required fact, 99.8% of claims are supported by the cited sources, about 1.7 cents per answer.

Have you measured your RAG pipeline, or are you trusting the defaults?

Repo and the full evaluation report in the first comment.

#RAG #AIEngineering #OpenSource #LLM #BuildInPublic

---

## First comment

Repo (Apache-2.0): <GITHUB_REPO_URL>
Evaluation datasets, method and every result: <GITHUB_REPO_URL>/tree/main/evals
Why these fusion settings (ADR 0008): <GITHUB_REPO_URL>/blob/main/docs/adr/0008-retrieval-fusion-and-sensitivity-routing.md
