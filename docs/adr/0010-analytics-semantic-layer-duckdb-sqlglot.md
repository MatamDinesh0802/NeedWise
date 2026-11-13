# 0010. Analytics: a semantic layer, SQL checked and rewritten with sqlglot, run in DuckDB

- Status: Accepted
- Date: 2026-11-12
- Context documents: ARCHITECTURE section 16; [docs/analytics.md](../analytics.md);
  [evals/README.md](../../evals/README.md) (text-to-SQL suite)

## Context
Phase 10 answers questions about tabular company data ("monthly revenue this year and the top
3 drivers") with a chart, a narrative, the SQL and the data. A language model writes the
queries. The requirements that shaped the design:

- **Permissions are decided in code, never by the model.** Who may query at all, and which rows
  each person sees: an account executive sees only the customers and deals he owns, whatever
  SQL the model writes.
- **The model must not be able to reach anything else:** other tables, files on the server, the
  network, database settings, the application database.
- **Definitions matter more than SQL syntax.** "Revenue" means invoice lines that are not void.
  A model that sums `amount_eur` gets a confident wrong number (Acme: 39,006,000 EUR instead of
  38,900,000).
- **The SQL is shown and can be re-run by the person**, so it must be ordinary, readable SQL.
- **Self-hosted, one container image, multi-tenant.** The first sources are uploaded CSV and
  XLSX files; live Postgres/MySQL sources come later.

## Decisions

### 1. A semantic layer in YAML, one per organization
Admins describe the queryable tables (an allow-list), many-to-one joins, named dimensions,
metrics with their filters (`revenue = sum(amount_eur) where status <> 'void'`), the last closed
period (`data_through`), who may query (`access`) and row policies. Every SQL fragment is parsed
with sqlglot when the model is saved, so a typo is an error for the admin, not a surprise at
query time. The agent's prompt carries the model (with each metric's SQL definition), not the
raw schema.

### 2. Two tools, one path for permissions
- `query_metrics`: metrics by dimensions, filters and a date range, compiled to SQL as a sqlglot
  tree. Request values become literal nodes; nothing is spliced into SQL text.
- `run_sql`: one SELECT, for what the semantic layer cannot express (two periods side by side,
  growth per customer, shares).

Both produce SQL that goes through the same guard and the same row-policy rewrite, so there is
one code path for permissions.

### 3. The guard: sqlglot parses, checks and rewrites; the rebuilt tree is what runs
One read-only query (SELECT or a set operation, with CTEs); table references only to model
tables or visible CTEs (DuckDB's scoping rules); no table functions, file paths, other schemas
or catalogs; a deny-list of file, network, configuration and catalog functions. Each row policy
is applied as a fence: the permitted rows of a restricted table are copied into a temporary
table in a statement of its own, and every reference to the table reads the fence under the
same alias, so joins, subqueries, set operations, recursive CTEs and window functions only see
permitted rows, and no condition of the query is evaluated on a hidden row. (The first version
replaced each reference with a filtered subquery in the same statement. DuckDB's optimizer was
free to reorder that: for a selective condition it ran the query's own `WHERE` on rows outside
the policy, and a failing `CAST` or `error()` quoted them. A review found it; see
[docs/analytics.md](../analytics.md#who-sees-what).) The person's values (`{user.email}`, read from the users table) are literal
nodes. A `LIMIT` of the row cap + 1 is added. The executed SQL is regenerated from the checked
tree and must parse back to itself; the text that was sent never runs.

### 4. DuckDB as the engine, sandboxed, as the second lock
Uploaded tables become a versioned DuckDB file per tenant. Each query opens its own connection,
read-only, with external access off (no files, no network), extension autoinstall and autoload
off, Python replacement scans off, the configuration locked, 2 threads, 512 MB and a timeout.
Even SQL that skipped the guard cannot read `/etc/passwd`, write a file or change a setting
(tests call the engine directly with each of these). DuckDB's memory limit and interrupt do not
cover everything (values built by scalar functions, a long call inside one function), so the
service runs queries in worker processes that are stopped above a memory limit or killed
after the timeout plus a grace period, a bounded number at once and one per person.

### 5. Numbers in the narrative are checked in code
Every number the model writes is matched against the result cells at the precision shown; one
repair pass names the unbacked ones; what remains is reported to the person, number by number.

### 6. Measured as each person, with a zero-leak gate
`nw eval --suites analytics` asks 51 questions as 8 Acme employees: execution accuracy against
gold SQL run as the same person, refusals for people without access, attacks, and a leak check
on every query result. Leaks fail the run.

## Rejected alternatives

**Raw text-to-SQL on the database schema** (the model sees the tables, writes SQL, a read-only
database role runs it). Simplest to build, and the common demo. Rejected because:
- accuracy depends on the model guessing definitions that live in people's heads (void lines,
  which date counts, what "pipeline" includes); the semantic layer writes them down once;
- safety rests on database grants alone: per-person row policies would need a database role or
  session variables per person and per query, and the model still sees every table name;
- schema drift reaches the prompt directly; with a semantic layer the admin decides what the
  model sees.

**A semantic-only query language** (the model may only send metric × dimension requests, as in
metrics-layer APIs). The safest option, and `query_metrics` is exactly this. Rejected as the
*only* tool because ordinary business questions do not fit it: growth per customer from Q1 to
Q3, a share of a total, two periods side by side, "top 3 drivers". Writing a new metric for each
question does not scale. We keep it as the preferred tool and allow SQL behind the same guard
and the same rewrite.

**Postgres foreign data wrappers** (load uploads into, or attach live databases to, PostgreSQL
and rely on its row-level security). Rejected because:
- analytical scans would run on the transactional application database, next to sessions,
  documents and the audit log;
- tenant uploads would live in the application database, with schema changes per upload;
- per-person row policies would mean `SET ROLE` or session variables for each query, and
  extension and credential management for each foreign server;
- DuckDB is in-process and columnar, needs no server, and a read-only file per dataset version
  gives atomic switches and simple cleanup.

## Consequences
- **Row policies are a SQL rewrite we own.** Fences do not depend on the order in which DuckDB
  evaluates conditions, but they copy the permitted rows of each restricted table per query,
  in memory: cheap for Acme, not measured on large tables. Tests run selective error oracles
  on every hidden row and must keep passing after every DuckDB upgrade.
- **Columns are not allow-listed.** Every column of a model table can be queried; data nobody
  may see must stay out of the dataset.
- **The function list is a deny-list.** The engine settings stop file and network access
  regardless; functions that read in-process state are only stopped by name.
- **Query results go to the configured model** (up to 50 rows per query in the tool loop, 100
  when composing). There is no local-only routing for analytics data yet, unlike restricted
  documents (ARCHITECTURE 11.6).
- **Live Postgres/MySQL sources** were left for a separate decision: the update below.
- **Execution accuracy with a real model** is measured by the suite above; the first results are
  recorded in `evals/README.md` and `docs/analytics.md`.

## Update: database sources (2026-11-13)

PostgreSQL and MySQL databases are now sources ([docs/analytics-sql-sources.md](../analytics-sql-sources.md)).
The decisions:

1. **A source is a dataset of its own**, with its own semantic model, access list and row
   policies, next to the uploaded files (`uploads`). Each model table names the table it reads
   (`source_table: schema.table`). Permissions stay per dataset, exactly as for uploads; a
   query reads one dataset. Rejected: one model spanning uploads and databases (it would need
   queries across engines, and one access list for data with different owners).
2. **The same guard, in the source's dialect, plus an allow-list for unknown functions.** A
   database can hold functions with any side effect (`dblink`, `pg_sleep`, user-defined ones),
   and a deny-list of names cannot know them. Functions sqlglot does not know by type must be
   ordinary built-ins on a per-dialect list; qualified calls, variables and assignments are
   refused. The guard rewrites model tables to the database's quoted tables, so the model's
   names are what people and the agent use and the database's never reach the prompt.
3. **Read-only three ways:** the guard; a read-only session per query (PostgreSQL:
   `default_transaction_read_only` and `BEGIN READ ONLY`, `search_path = pg_catalog`; MySQL:
   `SET SESSION TRANSACTION READ ONLY` and `START TRANSACTION READ ONLY`, no multi-statements,
   no `LOCAL INFILE`); and a database role that may only `SELECT`, which the docs require.
   Statement timeouts on the server, the row cap in the SQL, and the existing query workers
   (memory limit, kill after the timeout) apply.
4. **Row policies as CTE fences.** A read-only session cannot create the temporary tables the
   DuckDB fences use. The fence is a CTE of its own at the head of the query, `MATERIALIZED` on
   PostgreSQL (planned alone, no outer condition pushed into it) and, on MySQL, materialized by
   turning off `derived_merge` and `derived_condition_pushdown` for the session. On PostgreSQL
   18 a test shows the difference: inlined, the query's `CAST` ran on hidden rows and quoted one
   in its error; materialized, it counted nothing. On MySQL 8.4 the same oracles did not fire
   with merging on either, so the settings are a precaution the tests do not prove necessary.
   Rejected: per-person database roles or session variables with the database's own row-level
   security (an admin would have to set up roles and policies per person in every database).
5. **One connection per query, in the query worker**, to the address the host resolved to and
   was checked against the SSRF rules of model providers (no link-local or metadata; private
   and loopback networks only when the operator lists them). Rejected: a connection pool in the
   API process (sessions would carry state between people, and a large value would land in the
   API's memory); not measured: what the connections cost under load.
6. **Passwords sealed like provider keys**, bound to the tenant, the source and where it
   connects, so a changed host needs the password again.

Consequences: the semantic compiler writes each dialect (MySQL has neither `date_trunc` nor
`FILTER`); the eval takes items about a source (`dataset`); the allow-list must grow when people
need a built-in it lacks (a refusal names the function); the source's database role, not
NeedWise, is the lock that holds if the guard and the session were both wrong. Execution
accuracy on a source has not been measured with a real model.
