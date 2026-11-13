# LinkedIn post #11: Letting an LLM write SQL against company data, safely

- **Status:** Draft, not posted
- **Milestone:** Phase 10 (business intelligence agent)
- **Visual (suggested, not made yet):** the Insights page from the demo walkthrough (`docs/media/demo.mp4`, "a chart with its SQL"): Carol's "Monthly revenue trend this year and the top 3 drivers" with the chart, the narrative and the SQL panel open. Beside it, the same page as Dan (account executive) asking "Show revenue by customer": only his 13 customers. 1080×1350.
- **Numbers from:** `evals/results/2026-11-12-v1.0-analytics.md` (the v1.0 run), `docs/analytics.md` (the row-policy oracle and how fences fix it), `evals/README.md` (the precision caveat), `docs/analytics-sql-sources.md` (PostgreSQL and MySQL, scripted models only).
- **Post only after** the repo is public.

---

## Post

"Just let the model write SQL" works great in a demo with one user who can see everything.

In a company, a sales rep must only see their own accounts, whatever SQL the model writes.

What I built in NeedWise:
• Tables are described once in a semantic layer: metrics, dimensions, joins, and who may see which rows
• The model writes SQL; code decides what runs. A parser checks every query (one SELECT, known tables only, no file or catalog access), and row policies are applied by rewriting the query, not by asking the model to add a WHERE
• Queries run read-only in a sandbox with time, memory and row limits
• The answer shows a chart, the SQL and the data, and every number in the narrative is checked against the query results

Measured on 51 questions with Claude Sonnet 5.5, asked as 8 different people:
→ 33/36 return the right result (5 of 7 for the sales rep with a row policy)
→ 5/5 people without access refused before any model call
→ 10/10 attacks without a leak, 0 leaks in all 51 answers
→ 184/184 numbers in the answers matched a query result
→ 6.8 s median, about 2.6 cents per answer

What didn't work, and it's the part I learned the most from. My first row policy was a subquery: "only invoice lines whose customer belongs to Dan". Correct SQL. A review found it leaked anyway. For a selective condition the database built the join from the other side, so the query's own conditions also ran on Diego's rows. A query that fails on purpose (a bad CAST) then quoted one of Diego's invoice lines in the error message. One hidden row per request.

The fix: copy the rows Dan may see into a temporary table first, and point every reference at it. Hidden rows never reach the query at all. Cost: about 3 ms. The same review found that WITH RECURSIVE duckdb_tables AS (SELECT * FROM duckdb_tables) read the database's own catalog, past the checks. The guard now resolves CTE names the way the database does.

Also honest: the number check compares at the precision written, so "about €1.6M", a total the model added up itself, counted as matched by a €1,560,000 cell. And PostgreSQL and MySQL sources are built and tested with scripted models only; their accuracy with a real model isn't measured yet.

If you've put text-to-SQL in front of business users: where does your row-level security live, in the database, the prompt, or the query rewriter?

Repo and the full evaluation in the first comment.

#TextToSQL #AIEngineering #DataSecurity #OpenSource #BuildInPublic

---

## First comment

Repo (Apache-2.0): https://github.com/MatamDinesh0802/NeedWise
The v1.0 text-to-SQL run (51 questions): https://github.com/MatamDinesh0802/NeedWise/blob/main/evals/results/2026-11-12-v1.0-analytics.md
How the guard and the row-policy fences work: https://github.com/MatamDinesh0802/NeedWise/blob/main/docs/analytics.md
Why a semantic layer, DuckDB and sqlglot (ADR 0010): https://github.com/MatamDinesh0802/NeedWise/blob/main/docs/adr/0010-analytics-semantic-layer-duckdb-sqlglot.md
