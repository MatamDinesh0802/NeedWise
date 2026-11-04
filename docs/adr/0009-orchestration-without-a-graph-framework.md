# 0009. Multi-agent orchestration in plain asyncio; single pipeline stays the default

- Status: Accepted
- Date: 2026-11-04
- Supersedes: the LangGraph choice in ARCHITECTURE sections 2 and 12.5

## Context
ARCHITECTURE section 12 planned a LangGraph run graph: supervisor, fan-out with `Send`,
Postgres checkpoints, and `interrupt()` for approvals. When we built Phase 5, three facts were
already settled:

- **The graph is shallow and fixed:** plan, then agents in parallel, then aggregate and
  validate, then compose. There are no cycles beyond one bounded follow-up search per agent.
- **Streaming and replay already exist.** Every run publishes events to a Valkey Stream
  (`run.started`, `plan`, `agent.step`, `token`, ...), and the browser reconnects with
  `Last-Event-ID` (Phase 4).
- **Runs are short:** 10–40 seconds. Checkpointing the steps of a 20-second run buys little; a
  run cut off by a restart ends with `run.failed` and can be asked again.

## Decision
1. **Orchestrate in plain asyncio** (`modules/agents/`):
   - `plan_question`: an embedding fast path, or an LLM planner with structured output.
   - `run_agent`: one parameterized department agent; agents are rows in `agent_definitions`.
   - `aggregate`: merges sources and translates citation markers; findings that cite nothing
     real are dropped.
   - `orchestrate`: per-agent timeouts, failure isolation, and a fallback to the single
     pipeline.

   No graph framework, no second state store, no psycopg dependency.
2. **Approvals (Phase 8) are stored proposed actions,** not suspended graphs. An agent that
   wants to act writes the proposal to PostgreSQL and ends its run; the decision starts a new run.
   Nothing waits in memory, and nothing is re-planned on resume.
3. **Agents only narrow access.** Every agent searches *as the asking user*, restricted to its
   collections. The orchestration can never see more than one plain search.
4. **The default is decided by measurement** (`ASSISTANT_MODE`, below).

## Measurement
Claude Opus 5.5 for every role. Questions are asked as their users; answers are judged
(`evals/results/2026-11-04-cross-*.md`, `2026-11-04-qa-auto.md`, `2026-11-04-routing.md`).

**Routing (36 questions):** the planner picks exactly the right agents for 36/36. The fast path
(embedding similarity, no LLM) takes 8 of 18 single-area questions and is never wrong. Its
thresholds (score ≥ 0.3, margin ≥ 0.15) leave a gap above the closest multi-area question
(margin 0.115).

**Cross-department (15 questions, 3 of them spanning three areas):**

| | Single pipeline | Agents | Auto |
|---|---|---|---|
| Every required fact | 14/15 | 15/15 | 15/15 |
| Cites every expected document | 14/15 | 15/15 | 15/15 |
| Three-area questions | 2/3 | 3/3 | 3/3 |
| Latency p50 | 6.6 s | 19.2 s | 22.3 s |
| Model calls / cost per answer | 1 / $0.022 | 5.1 / $0.098 | 5.0 / $0.095 |

**Single-department (82 QA questions), auto vs single:** 80/82 vs 81/82 correct. The extra miss
was a bug: agents found nothing and the run didn't fall back; fixed. Time to first token p50
is 4.4 s vs 2.7 s, and cost per answer $0.025 vs $0.017.

**Reading:** a single hybrid search over 8 passages already reaches both areas of a two-area
question. Specialists help when a question spans three areas, because then each area gets its
own search budget. They cost 4–5× more and are about 3× slower.

**Default: `ASSISTANT_MODE=single`.** `auto` is a good choice where cross-department briefings
are common and latency matters less. `agents` exists for comparison.

## Consequences
- Adding an agent is configuration: an admin API now, an admin UI in Phase 11.
- Durable, long-running automation (scheduled workflows, Phase 8) will run on Celery with its
  own state in PostgreSQL, as ARCHITECTURE section 14 already planned. We revisit a graph
  framework only if a workflow needs resumable multi-step state that this model can't express.
