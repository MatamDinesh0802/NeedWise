# LinkedIn post #13: v1.0: what I learned building an open-source enterprise AI platform

- **Status:** Draft, not posted
- **Milestone:** Phase 12 (production hardening) and the v1.0.0 release
- **Visual (suggested, not made yet):** a one-page v1.0 scorecard, 1080×1350: the leak count (0 in 68 attacks), answers with every fact (81/82), time to first token (0.9 s), cost per answer ($0.0071), with a "what is not done" column beside it (live Drive and Slack, a cloud cluster, an external penetration test). Build it as HTML in `docs/devlog/assets/` like the earlier images, from `docs/benchmarks/v1.0.md`. Or the 1 min 32 s walkthrough (`docs/media/demo.mp4`) as native video.
- **Numbers from:** `evals/results/2026-11-13-v1.0.0-core.md` (the release check), `docs/benchmarks/v1.0.md` (the full benchmark, costs, the 200-person estimate), `docs/benchmarks/load.md`, `docs/case-study.md`.
- **Post only after** the repo is public and v1.0.0 is published (the GitHub Release and images).
- **Before posting:** the release check could not run every suite (the model provider's credit ran out). If they have been re-run on the release by then, update the numbers and drop the sentence about it.

---

## Post

NeedWise v1.0 is out: an open-source AI assistant for company knowledge that only answers from what each person is allowed to see.

13 phases, built in public. Here's what I'd tell myself at the start.

1. Measure the defaults you're told to use.
Textbook hybrid search scored worse than either of its two inputs on my questions. Multi-agent orchestration helped on 1 question in 15, at 4–5× the cost. Both would have shipped as defaults without the measurement.

2. Make the safety property a string match.
Every restricted document carries a canary token. If one shows up where it shouldn't, that's a leak, no judgement needed. That's what lets it gate every change.
Release check: 0 leaks across 68 attacks on four surfaces (chat, department agents, the research agent, text-to-SQL), and 0 times the model did what an attack asked.
Caveat: 0 only means 0 for the attacks I wrote.

3. Test security with a model that obeys.
If a scripted model does exactly what the injected instruction says and the test still passes, the code stopped it, not the prompt. Most of the security review's fixes were found that way.

4. Unit tests won't find what the live run finds.
Permission checks that disagreed for auditors. Streams cut after 5 quiet seconds. A JSON schema that silently dropped every field named "title". A request router that had never once worked with the real model. All invisible with scripted models.

5. The first bottleneck is your own code.
Load testing with a stub model: at 400 simulated users, the API spent 27% of its event loop writing and reading one event per streamed word. Batching them, plus a second API process, brought latency back to unloaded levels. Not the database, not the vector store.

The numbers, with Claude Sonnet 5.5 on a fictional company (20 modelled employees, 49 documents, 82 test questions):
→ 81/82 answers with every required fact
→ 0.9 s to the first token (median)
→ $0.0071 per answer
→ Estimated $80–140 a month for a 200-person company (arithmetic on measured costs and assumed usage, not a deployment)

What didn't go to plan, even at the end: the release check ran out of API credit halfway, so part of the v1.0 numbers come from the full benchmark the day before. Both are published, with dates and commits.

Not done yet: live Google Drive and Slack sync (tested against fakes of their APIs), a cloud Kubernetes cluster, an external penetration test, local models measured end to end. All listed in the release notes.

It's Apache-2.0 and self-hosted. One command gives you the stack and the demo company.

What would you need to see before trusting an AI assistant with your company's documents?

Repo, release notes and the benchmarks in the first comment.

#OpenSource #EnterpriseAI #RAG #AIEngineering #BuildInPublic

---

## First comment

Repo (Apache-2.0): https://github.com/MatamDinesh0802/NeedWise
v1.0.0 release notes: https://github.com/MatamDinesh0802/NeedWise/blob/main/docs/releases/v1.0.0.md
v1.0 benchmarks, with the method and the limitations: https://github.com/MatamDinesh0802/NeedWise/blob/main/docs/benchmarks/v1.0.md
The case study (what went wrong, what it costs): https://github.com/MatamDinesh0802/NeedWise/blob/main/docs/case-study.md
