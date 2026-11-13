# LinkedIn post #6: Do you need multi-agent? I measured it.

- **Status:** Draft, not posted
- **Milestone:** Phase 5 (department agents)
- **Image:** `assets/post-06-multi-agent-measured.png` (1080×1350). Numbers come from `evals/results/2026-11-04-cross-single.md`, `2026-11-04-cross-agents.md` and `2026-11-04-routing.md`; the missed question is `xd-14` in `2026-11-04-cross-single.json`. Small multiples with a zero baseline each, because facts, seconds and dollars don't share a scale.
- **Post only after** the repo is public.
- **Before posting:** run the red-team suite with `ASSISTANT_MODE=agents` (and `auto`). The agent runs above didn't include it ("0 retrieval checks, 0 red-team answers"), so this draft claims the access *design* for agents, not a measured leak rate. Once it passes, add "0 leaks with agents too" to the post. Re-run the cross-department and routing suites too: they ran at commit `57c9802`, before the research agent and governed tools (`4f728f1`) landed, so the agent roster has changed since.

---

## Post

"Use multi-agent" is the new "use hybrid search".

So I measured it before making it the default.

The setup: 15 questions that span departments, each asked as a specific employee of a fictional company. For example: "For my on-call week: how do I restart the fleet controller, what P1 response time do customers get, and how close may I get to a moving AMR-500?" That's engineering, support and operations in one sentence.

Two ways to answer it:
1. One permission-aware search over everything that person can see
2. A planner splits the question, then department agents each search their own collections in parallel, as that person

Results, with Claude Opus 5.5 in every role:
→ Every required fact: 14/15 vs 15/15
→ Median time to a complete answer: 6.6 s vs 19.2 s
→ Cost per answer: 2.2¢ vs 9.8¢
→ Model calls per answer: 1 vs 5.1

The one miss was exactly that on-call question. One search found the restart runbook and stopped there; it never got to the P1 response time or the 1.5-metre safety distance. Three agents each took one part.

So agents win one question in fifteen, at about 3× the time and 4.5× the cost. That's not a reason to always use them. It's a reason to route.

What shipped:
• An "auto" mode: questions about one area stay on the fast single search; only multi-area questions go to agents
• Routing is measured too: 36/36 decisions right across one-area, multi-area and general questions. 8 were matched instantly against example questions, without calling the planner at all
• Agents can only narrow access: each one searches as the person asking, restricted to its own collections. An agent never sees more than a plain search would
• Admins can add or change an agent (its collections, example questions, how many searches) without a deploy

One thing I didn't expect: I'd planned on a graph framework for the orchestration. The flow turned out shallow and fixed (plan → agents in parallel → merge → answer), so it's about 700 lines of plain async Python, with per-agent timeouts and a fallback to the single search.

Caveat: 15 questions is a small set. I'll grow it, because the questions that cross departments are the ones that decide this.

When did adding agents actually pay off in your system, and how did you know?

Repo, the evaluation reports and the design decision in the first comment.

#AIAgents #MultiAgent #AIEngineering #OpenSource #BuildInPublic

---

## First comment

Repo (Apache-2.0): https://github.com/MatamDinesh0802/NeedWise
The comparison, 15 cross-department questions: https://github.com/MatamDinesh0802/NeedWise/blob/main/evals/results/2026-11-04-cross-agents.md (agents) and https://github.com/MatamDinesh0802/NeedWise/blob/main/evals/results/2026-11-04-cross-single.md (one search)
Routing evaluation: https://github.com/MatamDinesh0802/NeedWise/blob/main/evals/results/2026-11-04-routing.md
Why plain asyncio instead of a graph framework (ADR 0009): https://github.com/MatamDinesh0802/NeedWise/blob/main/docs/adr/0009-orchestration-without-a-graph-framework.md
