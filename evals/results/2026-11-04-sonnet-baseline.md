# WorkMind evaluation: 2026-11-04-sonnet-baseline

- date: `2026-11-04 12:04 UTC`
- commit: `def3f00`
- tenant: `Acme Robotics (restricted_data: allow)`
- indexed chunks: `294`
- embedding model: `embeddinggemma-300m (768 dims)`
- hybrid fusion: `RRF k=2, BM25 weight 0.5, trust boost on`
- answer model: `claude-sonnet-5-5`
- judge model: `claude-sonnet-5-5`
- assistant: `single (fast path: score >= 0.3, margin >= 0.15)`
- datasets: `82 QA, 16 red team, 6 abstention, 0 cross-department, 36 routing`

## Permission safety (gate: 0 leaks)

**PASS**: 0 leak(s) across 48 retrieval checks and 16 red-team answers.

## Retrieval

| Mode | Doc Recall@1 | Doc Recall@5 | Doc MRR | Evidence@1 | Evidence@3 | Evidence@5 | Evidence MRR | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|
| hybrid | 98.8% | 100.0% | 0.994 | 92.7% | 98.8% | 98.8% | 0.957 | 36 ms | 81 ms |
| dense | 96.3% | 100.0% | 0.982 | 90.2% | 98.8% | 98.8% | 0.945 | 33 ms | 90 ms |
| sparse | 79.3% | 92.7% | 0.858 | 72.0% | 86.6% | 86.6% | 0.793 | 12 ms | 18 ms |

Doc: any passage from an expected document. Evidence: the passage that contains
a required fact (stricter: the right chunk, not just the right file).

### Hybrid, by category

| Category | Questions | Doc Recall@5 | Evidence@3 |
|---|---|---|---|
| conflict | 1 | 1/1 | 1/1 |
| engineering | 4 | 4/4 | 4/4 |
| finance | 1 | 1/1 | 1/1 |
| guest | 1 | 1/1 | 1/1 |
| hr | 2 | 2/2 | 2/2 |
| identifier | 12 | 12/12 | 12/12 |
| marketing | 1 | 1/1 | 1/1 |
| operations | 2 | 2/2 | 2/2 |
| paraphrase | 30 | 30/30 | 29/30 |
| policy | 14 | 14/14 | 14/14 |
| restricted | 4 | 4/4 | 4/4 |
| sales | 3 | 3/3 | 3/3 |
| slides | 1 | 1/1 | 1/1 |
| support | 2 | 2/2 | 2/2 |
| table | 4 | 4/4 | 4/4 |

## Routing (planner and fast path)

Exact agent set: **34/36**. Fast path used for 8 questions, 8 of them correct. Planner latency p50 1.8 s.

| Expected | Questions | Correct |
|---|---|---|
| single | 18 | 16/18 |
| fan_out | 15 | 15/15 |
| direct | 3 | 3/3 |
- ro-s06: expected ['finance'], got ['engineering', 'finance'] (planner)
- ro-s16: expected ['marketing'], got ['finance', 'marketing'] (planner)

## Answers

| Metric | Value |
|---|---|
| Answer correctness (all required facts) | 81/82 |
| Cites an expected document | 82/82 |
| Answers with invalid citation markers | 0/82 |
| Wrongly abstained | 1/82 |
| Restricted sources withheld by policy | 0/82 |
| Faithfulness (judge, mean) | 99.4% |
| Errors | 0/82 |

## Abstention

Correctly said it could not find the answer: 6/6

## Latency and cost (answers, including retrieval)

| Metric | p50 | p95 |
|---|---|---|
| Time to first token | 1.2 s | 7.9 s |
| Complete answer | 2.5 s | 8.7 s |

Mean tokens per answer: 2345 in / 165 out. Mean cost per answer: $0.0066.

## Answers to inspect

### qa-01 (alice): How many weeks of parental leave do employees get?
- unsupported claim: In the thread, Alice Moreau asked people not to plan around any number other than the official one.

> Employees get 16 weeks of fully paid parental leave, regardless of gender, effective 2026-01-01 [S1]. This applies equally to birth, non-birth, adoptive and foster parents [S1].
> 
> Key details:
> - The leave can be taken as one continuous block, or in several periods within the first year after the child's birth or placement if your manager agrees [S1].
> - Notify HR of your intended dates as early as reasonably possible so cover can be planned [S1].
> 
> A Slack thread claims parental leave is now 20 weeks. That is a chat discussion, not policy, and no official source confirms it. In the thread, Alice Moreau asked people not to plan around any number other than the official one [S7].

### qa-12 (dan): Do I have to declare gifts from suppliers?
- unsupported claim: For supplier gifts up to €50, you don't have to declare it.

> It depends on the value and type of gift:
> 
> - **Up to €50:** You don't have to declare it. You may accept it if it is occasional and not linked to a pending decision [S1].
> - **Above €50:** Yes, declare it to Finance (Hannah Schmidt's team), either before or immediately after accepting [S1].
> - **Cash or cash equivalents (vouchers, gift cards):** Never accept these, whatever the value [S1].
> - **Hospitality during an active tender or negotiation:** Decline it and inform your manager [S1].
> 
> When in doubt, declare. A declaration is never a problem, but an undisclosed gift can be [S1].

### pp-08 (dan): A vendor sent me a bottle of wine worth about 80 euros. What should I do?
- unsupported claim: The user's wine is above the €50 limit.

> Declare the gift to Finance (Hannah Schmidt's team), either before or immediately after accepting it, because it is worth more than €50 [S1].
> 
> Some details that may matter:
> - Gifts from a supplier worth up to €50 can be accepted if they are occasional and not linked to a pending decision. Your wine is above that limit, so the declaration requirement applies [S1].
> - The sources don't say whether you must keep or return a gift above €50. They only require you to declare it [S1].
> - If the vendor is involved in an active tender or negotiation, hospitality should be declined and your manager informed [S1]. The sources list this rule under hospitality, so check whether it applies to your situation.
> - When in doubt, declare. A declaration is never a problem, but an undisclosed gift can be [S1].

### pp-16 (samuel.mensah): How fast must we react when a customer's whole fleet is down?
- missing facts: ['1 hour', 'one hour']

> I couldn't find this in the sources available to you. The sources don't state a response or resolution time target for a fleet-wide outage.
> 
> What they do cover:
> 
> - **Priority:** A fleet-wide FleetOS outage is classed as P1 – Critical (operations stopped) [S1].
> - **Escalation timing:** The on-call support engineer handles the first response and initial diagnosis. The Support Lead, Samuel Mensah, is brought in automatically if a P1 is not stabilised within 4 hours, or if any SLA target is at risk [S2].
> - **Executive reporting:** A P1 lasting more than 24 hours is reported to the CTO, Marcus Bell, and the Head of Operations, Olivia Grant [S2].
> - **Engineering:** FleetOS platform issues are escalated with Staff Engineer Wei Zhang as technical point of contact [S2].
> - **Holidays:** Customers with a 24/7 support contract are covered by the on-call rotation on company holidays [S6].

### pp-17 (bob): How close can I walk to one of the big robots while it is driving?
- unsupported claim: The AMR-500 is the larger robot model.

> You must stay at least 1.5 metres away from a moving AMR-500 (the larger robot model). This applies at all times, whether the robot is loaded or unloaded, and whether it is travelling or manoeuvring at a station [S2].
> 
> If you need to enter a robot lane, for example for cleaning or restocking, use the FleetOS "Zone Hold" function first [S4]. Only trained staff may pause, override or manually move a robot [S4]. High-visibility vests are required on any floor where AMR-500 robots operate [S4].
> 
> The sources don't name the "big robot" explicitly. I've assumed you mean the AMR-500, since the sources only give a numeric exclusion distance for that model. The AMR-200 slows and stops automatically when it detects a person, but you should still give it right of way in aisles and never step over or onto it [S2].

