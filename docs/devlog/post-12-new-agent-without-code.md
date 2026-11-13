# LinkedIn post #12: Adding a new AI agent to a company without writing code

- **Status:** Draft, not posted
- **Milestone:** Phase 11 (admin, governance and usage analytics)
- **Visual (suggested, not made yet):** Admin › Agents with the Legal agent's editor open (description, collections, example questions) and the test console beside it routing "Can I sign a 30,000 euro supplier contract myself?" to the Legal agent. The demo walkthrough ends on this screen (`docs/media/demo.mp4`, "an administrator adding an agent"). 1080×1350.
- **Numbers from:** `evals/results/2026-11-12-routing-legal-ui.md` (the agent made in the browser), `evals/results/2026-11-12-routing-legal.md` and `-after-fix.md` (the planner rule), `docs/agents.md`.
- **Post only after** the repo is public.

---

## Post

The test for "agents as configuration" is simple: can an admin add one without a developer?

So I did exactly that. Signed in as the IT administrator of the fictional company, opened Admin › Agents, and created a Legal agent in the browser. No code, no deploy.

What the admin fills in:
• A description written for routing: what it answers (signature authority, NDAs, data processing agreements, export control…) and what it is not for (employment contracts go to HR, a customer's renewal date to Sales)
• The collections it may search. They only narrow what the person asking can see; an agent never adds access
• Read-only tools, a model, limits on searches and output
• Then a test console that plans and answers with the unsaved draft, so you see the routing before anyone else does

Every save is a numbered version you can compare and restore.

The measured part: 56 routing questions, 15 of them legal, 3 near misses that sound legal but aren't: employment-contract questions that belong to HR.

With Claude Sonnet 5.5 planning:
→ Legal picked for 15 of 15 legal questions (14 with exactly the right agents)
→ Picked for none it shouldn't, 3/3 near misses kept away
→ 53/56 plans exactly right overall
→ 46 planner calls, 16 cents for the whole run

What didn't work: adding the Legal agent broke a question that has nothing to do with law. "What is Project Heron?" went to no agent at all, in 5 of 5 plans. Without Legal in the list, it went to Leadership, 5 of 5. The planner's rule for answering directly said "general, chit-chat, or not tied to any area above", and an unknown project name fits that literally. One more agent in the list tipped it.

The rule now says "needs none of the company's documents". I also tried a longer wording that fixed Project Heron but added an extra agent to two marketing questions. I didn't keep it.

The lesson: adding an agent is a change to every other agent's routing. Run the routing evaluation after each one, not just the new agent's questions.

If you run several agents: how do you check that a new one didn't change what the others get?

Repo, the step-by-step and the routing reports in the first comment.

#AIAgents #MultiAgent #NoCode #OpenSource #BuildInPublic

---

## First comment

Repo (Apache-2.0): <GITHUB_REPO_URL>
Create the Legal agent step by step, and what was measured: <GITHUB_REPO_URL>/blob/main/docs/agents.md
Routing with the agent made in the browser: <GITHUB_REPO_URL>/blob/main/evals/results/2026-11-12-routing-legal-ui.md
The rejected wording: <GITHUB_REPO_URL>/blob/main/evals/results/2026-11-12-routing-legal-rejected-rule.md
