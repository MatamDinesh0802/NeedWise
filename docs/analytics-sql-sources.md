# Analytics on PostgreSQL and MySQL

Insights can answer from a live PostgreSQL or MySQL database, next to the uploaded CSV and XLSX
tables ([analytics.md](analytics.md)). An org admin connects the database, picks the tables
people may ask about and writes a semantic model for them, like the one for uploaded tables. The
database is then a **dataset** of its own: its own model, its own access list, its own row
policies. The BI agent, `POST /analytics/query`, `describe_data` / `query_data` (research mode,
MCP) and the evaluation all use it through the same path as uploaded data: the semantic compiler
and the SQL guard (in the database's dialect), the person's row policies as fences, a worker
process with memory and time limits, number verification and charts.

Code: `apps/api/src/workmind/modules/analytics/` (`sources.py`, `remote.py`, the dialect parts
of `guard.py`, `compiler.py` and `semantic.py`), the routes in `api/v1/analytics.py`, the table
`analytics_sources` (migration `0025`), Admin › Analytics › Databases in the web app. Design
decisions: [ADR 0010](adr/0010-analytics-semantic-layer-duckdb-sqlglot.md#update-database-sources-2026-11-13).

## Read-only, three ways

A query reaches the database only if all three agree it reads.

1. **The guard** (`guard.py`, sqlglot in the database's dialect) accepts exactly one `SELECT`
   (or a set operation, with CTEs) over the model's tables, as for uploaded data, and refuses at
   any depth: DML and DDL, `COPY`, `LOAD DATA`, `SELECT ... INTO` / `INTO OUTFILE`, `SET`,
   `CALL`, `DO`, `PREPARE`, `EXPLAIN`, `VACUUM`, `LISTEN` / `NOTIFY`, transactions, locking
   clauses (`FOR UPDATE`, `LOCK IN SHARE MODE`), data-modifying CTEs, other schemas and
   catalogs (`information_schema`, `pg_catalog`, `mysql.*`), tables outside the model (also in
   an allowed schema), table functions (`dblink(...)`, `generate_series(...)` as a table),
   parameters, MySQL variables and assignments (`@x`, `@@version`, `@x := 1`) and qualified
   function calls (`schema.f()`). Functions: a database can hold functions of its own, with any
   side effect, so a function sqlglot does not know by type must be on a short allow-list of
   ordinary built-ins of that dialect (`ALLOWED_FUNCTIONS` in `guard.py`: date, text, number,
   JSON and aggregate functions). `pg_sleep`, `dblink`, `lo_import`, `pg_read_file`,
   `query_to_xml`, `set_config`, `SLEEP`, `BENCHMARK`, `LOAD_FILE`, `GET_LOCK` and any
   user-defined function are unknown names, so they are refused. The deny-list of the uploaded
   data also applies, with additions for databases (waiting, locks, files, settings, SQL in a
   string) and the functions that name the connection's user or database (`current_user`,
   `session_user`, `current_database`, `database()`, `user()`, `connection_id()`); `version()`
   is allowed. Comments never reach the
   database: the SQL that runs is rebuilt from the parsed tree, so MySQL's executable comments
   (`/*! ... */`) are dropped with the rest.
2. **The session** (`remote.py`, in the query worker). One new connection per query, never
   shared:
   - PostgreSQL: the startup packet sets `default_transaction_read_only = on`,
     `statement_timeout`, `lock_timeout` and `idle_in_transaction_session_timeout` (the query
     timeout), `search_path = pg_catalog` (an unqualified name never finds a function, operator
     or table of the database's own schemas) and `standard_conforming_strings = on` (a
     backslash in a literal is a backslash, as sqlglot writes literals); the query runs in
     `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY` as one prepared statement (the extended
     protocol carries exactly one statement).
   - MySQL: a fixed `sql_mode` (MySQL 8's defaults: backslash escapes on, no `ANSI_QUOTES`, as
     sqlglot writes), `max_execution_time`, `SET SESSION TRANSACTION READ ONLY`, then
     `START TRANSACTION READ ONLY`, the query, `ROLLBACK`. PyMySQL is given neither
     `CLIENT.MULTI_STATEMENTS` nor `LOCAL INFILE`, and the query text is sent without
     `%`-formatting.

   Tests run, as a user with write privileges, `DELETE`, `UPDATE`, `INSERT`, `CREATE TABLE`,
   `DROP TABLE` and `SELECT 1; DELETE ...` directly through this layer (no guard): every one
   fails and no row changes, on PostgreSQL 18 and MySQL 8.4.
3. **The database role.** Give WorkMind a user that may only read the tables people may ask
   about. This is the lock that does not depend on WorkMind's code. For PostgreSQL:

   ```sql
   CREATE ROLE workmind_reader LOGIN PASSWORD '...';
   GRANT CONNECT ON DATABASE sales TO workmind_reader;
   GRANT USAGE ON SCHEMA shop TO workmind_reader;
   GRANT SELECT ON shop.customers, shop.orders TO workmind_reader;  -- or ON ALL TABLES IN SCHEMA
   ALTER ROLE workmind_reader SET default_transaction_read_only = on;
   ```

   For MySQL:

   ```sql
   CREATE USER 'workmind_reader'@'%' IDENTIFIED BY '...';
   GRANT SELECT ON shop.customers TO 'workmind_reader'@'%';
   GRANT SELECT ON shop.orders TO 'workmind_reader'@'%';
   ```

   Prefer a replica: analytical queries can be heavy, and the statement timeout bounds their
   time, not their load.

## Who can be reached (server-side request forgery)

A source's host is checked when it is saved and resolved again before **every** connection:

- never: link-local addresses (`169.254.0.0/16`, `fe80::/10`), cloud metadata endpoints
  (`169.254.169.254`, `metadata.google.internal`, `fd00:ec2::254`, `100.100.100.200`, ...),
  multicast and unspecified addresses, also when a name resolves to one or IPv6 maps one;
- private, loopback and other non-public addresses only inside the networks the operator lists
  in `ANALYTICS_SOURCE_PRIVATE_NETWORKS` (comma-separated CIDRs; empty by default, so a fresh
  deployment reaches public addresses only). An org admin cannot widen this: it is a deployment
  setting. In compose, where databases usually sit on the Docker network, list that network
  (for example `172.16.0.0/12`); in Kubernetes, the pod or service CIDR of the databases (Helm:
  `config.env.ANALYTICS_SOURCE_PRIVATE_NETWORKS`);
- public addresses: allowed.

The connection goes to the address that was just checked (a name that resolves elsewhere a
moment later, DNS rebinding, is not followed). With `tls_mode: verify-full` it goes to the name
instead, because the certificate must match it; a rebinding server would need a certificate for
that name. A name that does not resolve can be saved (it may only resolve inside the
deployment's network) but cannot be connected to.

Mind what you allow: a listed network includes every database in it, WorkMind's own PostgreSQL
too if it is there. A source still needs a user name and password that work.

## The password

Stored like model provider keys ([models.md](models.md)): AES-256-GCM with
`SECRETS_ENCRYPTION_KEY`, the key id in the ciphertext, bound as associated data to the tenant,
the source and where it connects (kind, host, port, database, user). A ciphertext copied to
another source or tenant does not open, and changing the host, port, database or user without
entering the password again is refused (`422 password_required`): an admin cannot point the
stored password at a server of their choosing. The API never returns it (`password_set: true`
says one is stored), audit events never contain it, and connection errors never quote it.
`wm secrets rotate` re-encrypts source passwords with the active key, next to the provider keys
(`modules/analytics/rotation.py`); keep the old key in `SECRETS_ENCRYPTION_PREVIOUS_KEYS` until
it reports no failures.

## Limits

| What | How | Setting |
|---|---|---|
| Time per query | statement timeout on the server; a worker with no answer 2 s after the timeout plus the connect timeout is killed, closing its connection | `ANALYTICS_TIMEOUT_S` (10) |
| Connecting | connect timeout | `ANALYTICS_SOURCE_CONNECT_TIMEOUT_S` (5) |
| Rows | `LIMIT cap + 1` in the SQL, at most `cap + 1` fetched | `ANALYTICS_MAX_ROWS` (1,000) |
| Memory | the query worker stops above the limit (a huge value cannot reach the API process) | `ANALYTICS_WORKER_MEMORY_MB` (1,024) |
| Result size | 8 MB as JSON | |
| Connections | at most N open at once to one tenant's sources, across every API process (Valkey leases); more queries wait their turn, up to the timeout, then `429 analytics_busy` | `ANALYTICS_SOURCE_CONNECTIONS` (4) |
| Per person | one query at a time, as for uploaded data | `ANALYTICS_QUERIES_PER_USER` (1) |

There is no connection pooling: each query opens and closes its own connection (TLS included).
That keeps sessions from leaking state between people; its cost was not measured beyond the
tests (a few milliseconds per query against a local server).

## The semantic model

The same YAML as for uploaded tables ([analytics.md](analytics.md#the-semantic-model)), with
two differences:

- every table names the table it reads, `source_table: schema.table` (MySQL:
  `database.table`), in one of the source's schemas. The model's own name for the table is
  what people, the agent and the SQL use; the guard rewrites each reference to the database
  table (`"shop"."orders" AS orders`). Column names must be lower_snake_case and are the
  database's own: a column named otherwise cannot be used (put a view in front of the table);
- the SQL fragments (metrics, filters, dimensions, row policies) are in the database's dialect.

When an admin saves a source's model it is validated in that dialect and checked against the
database: every table must be in the source's schemas and exist (and be readable by the
source's user), every column must exist; otherwise `422 invalid_model` lists the problems.

The compiler writes each dialect: PostgreSQL gets `date_trunc` and `FILTER (WHERE ...)`; MySQL
has neither, so time grains are built from `MAKEDATE`, `INTERVAL` and `WEEKDAY`, and a filtered
metric becomes `SUM(CASE WHEN ... THEN x END)` (`COUNT(DISTINCT CASE ...)`, `COUNT(CASE ...
THEN 1 END)`): the same values, since rows that fail the condition add NULL, which aggregates
skip. MySQL puts NULL groups first when sorting ascending (it cannot say `NULLS LAST` next to a
column position). Before a semantic query with joins runs, the many-to-one check of the uploaded
data runs on the database too (does the one side's key repeat?), and is redone every 5 minutes.

The fictional shop database used by the tests and the evaluation is in
[`seed/acme/analytics/sql/`](../seed/acme/analytics/sql/): `shop.sql` (customers, 87 orders,
an `internal_notes` table left out of the model, an `hr.salaries` table outside the schema) and
`shop.yaml`, the same model for PostgreSQL and MySQL.

## Row policies on a database

For uploaded data, the rows a person may see of a restricted table are copied into a temporary
table first (a fence; [analytics.md](analytics.md#who-sees-what)). A read-only session cannot
create one (and a read-only role should not be allowed to), so on a database the fence is a CTE
of its own, first in the query, and every reference to the table reads it:

```sql
-- sent, as Dan
SELECT c.region, sum(o.amount_eur) FROM orders o JOIN customers c USING (customer_id) GROUP BY 1
-- executed on PostgreSQL
WITH _wm_rows_customers AS MATERIALIZED (
       SELECT * FROM "shop"."customers" AS customers
       WHERE (customers.account_owner = 'dan@acme.test')),
     _wm_rows_orders AS MATERIALIZED (
       SELECT * FROM "shop"."orders" AS orders
       WHERE (orders.customer_id IN (SELECT customer_id FROM "shop"."customers" AS customers
                                     WHERE account_owner = 'dan@acme.test')))
SELECT c.region, SUM(o.amount_eur) FROM _wm_rows_orders AS o JOIN _wm_rows_customers AS c ...
LIMIT 1001
```

On PostgreSQL a `MATERIALIZED` CTE is planned on its own and no condition of the outer query is
pushed into it, so the query's own conditions only ever see permitted rows. This was checked,
not assumed: on PostgreSQL 18, the same oracle query with the fence inlined (`NOT
MATERIALIZED`) fails on each of three of Diego's orders with `invalid input syntax for type
integer: "direct"` (a hidden row's value, in the error), and counts nothing with `MATERIALIZED`
(`test_postgres_fences_must_be_materialized`). On MySQL the session turns off
`derived_merge` and `derived_condition_pushdown`, so the CTE is materialized before the query
reads it. Honestly: on MySQL 8.4 the same oracle queries did not fail with those settings on
either, so the tests do not show that they are needed; they are there because MySQL documents
that it may merge a CTE into the outer query or push conditions into it. Settings that MySQL
5.7 or 8.0 before 8.0.22 does not know make the query fail: MySQL 8.0.22 or later is needed.

Tests run, as Dan, a selective failing condition (PostgreSQL: `CAST(name AS INTEGER)`; MySQL:
`JSON_EXTRACT(name, '$')`) on each of the 6 customers and 10 orders hidden from him: each runs
and counts nothing, while the same query as Diego fails (so the oracle is real). Errors about a
value are reported by kind only, never quoting it.

## The API

All under `/api/v1/admin/analytics`, org owners and admins; problem documents with a `code`.

| Route | What |
|---|---|
| `GET /sources` | the sources: key, name, kind, host, port, database, user, `password_set`, TLS mode, CA, schemas, enabled, the model's YAML, `checked_at`, `server_version`, `last_error` |
| `POST /sources` `{key, name, kind, host, port?, database, username, password, tls_mode, tls_ca?, schemas, enabled?}` | 201; `422 invalid_source` for a host that may not be reached; `409 conflict` for a key in use |
| `PATCH /sources/{key}` | change any field; `422 password_required` when the host, port, database or user changes without the password |
| `DELETE /sources/{key}` | 204; the database is not touched |
| `POST /sources/{key}/test` | connect as queries do and ask the server's version: `{ok, server_version, elapsed_ms, error}` |
| `GET /sources/{key}/catalog` | the tables and columns of the source's schemas its user can read (from `information_schema`): `{tables: [{schema, name, kind, columns: [{name, type, model_type, usable}]}], truncated}`; `503 source_unavailable` with the reason |
| `PUT /sources/{key}/model` `{yaml}` | validate and check against the database, then store; `422 invalid_model` with the problems |

For everyone else, a source is one more dataset:

- `GET /analytics/model` lists every dataset the caller may query in `datasets`, each with its
  `key`, `kind` (`files`, `postgres`, `mysql`) and `dialect`;
- `POST /analytics/query {"sql", "dataset": "shop"}` runs SQL in that dataset's dialect;
- an answer's queries carry their `dataset` and `dialect`, so the SQL shown re-runs there;
- a person asking about a source that cannot be reached gets `503 source_unavailable` (or, from
  the agent, a tool error it reports) without the host or the reason; admins see the reason in
  `last_error`.

Every query is audited as for uploaded data, with the dataset's key; source changes are audited
as `analytics.source.created`, `.updated`, `.deleted` and `analytics.source.model.updated`
(where it connects, never the password).

## In the web app

Admin › Analytics › Databases: connect a database (the form checks what the API checks), test
the connection, turn a source off, replace its password, list the tables its user can read,
tick some and write a starting model from them (each with its `source_table`, its usable
columns, its first date column as the time column, a row count per table), then edit and save
it. Pointing a source at another host, port, database or user is done with `PATCH` (or by
removing and adding it); the page has no form for that yet.

On Insights the model panel describes each dataset under its kind, the examples come from every
dataset, and a query on a database shows where it ran (`shop · PostgreSQL`).

## The agent

With the uploaded tables alone, the agent's prompt is the one from before. With a database
source, each dataset is rendered under its key with its SQL dialect, and the prompt says that a
query reads one dataset (tables of different datasets cannot be joined) and that `run_sql`
takes the dataset's dialect; `query_metrics` and `run_sql` take `dataset`. A tool call that
names no dataset when there are several, or one the person may not query, gets a tool error
saying which exist.

## Evaluation

[`evals/datasets/analytics_sql.jsonl`](../evals/datasets/analytics_sql.jsonl): 19 items about
the shop source (key `shop`), asked as the Acme people: 10 with gold SQL written to run on both
PostgreSQL and MySQL (revenue, by region, monthly, refunds, channels, top customers, open
orders, customers per segment, and two as Dan under "Own shop accounts only"), 3 from people
without access (Bob, Mallory, Erin), and 6 attacks (`hr.salaries`, `internal_notes`, a
`DELETE`, `pg_sleep`, `information_schema`, Dan asking for Diego's accounts). To run it:

1. load `seed/acme/analytics/sql/shop.sql` into a schema `shop` (and `hr`) of a PostgreSQL
   database, or databases `shop` and `hr` on MySQL, and create the read-only user above;
2. connect it in Admin › Analytics as key `shop` with schema `shop`, and save
   `seed/acme/analytics/sql/shop.yaml` as its model;
3. `uv run wm eval --skip-retrieval --suites analytics --analytics-file analytics_sql.jsonl`.

Each item runs on its dataset with that dataset's reference person (who sees every row) for the
leak checks, and the row counts of the source's tables are compared before and after. **It has
not been run with a real model.** With a scripted model that answers each question with its gold
SQL, on both PostgreSQL 18 and MySQL 8.4, all 19 pass with 0 leaks
(`test_the_eval_items_hold_and_score_on_a_sql_source`); that checks the items and the scoring,
not the agent.

## Measured

What the tests ran, on an Apple M5 against PostgreSQL 18.6 (a throwaway database on the
development server) and MySQL 8.4.13 (a throwaway container), with scripted models:

- 127 guard cases that must be refused (54 statements in both dialects, as a person with and
  without a row policy, 12 MySQL-only, 7 PostgreSQL-only;
  `tests/test_analytics_sources.py`);
- 51 integration tests on the two databases (`tests/integration/test_analytics_sources.py`:
  read-only session, one statement, server timeout, row cap and types, the read-only role,
  `search_path`, `LOAD DATA LOCAL`, wrong passwords, admin flow, catalog, model checks, Dan's
  rows and the error oracles, attacks through the service, private hosts, key rotation, a
  moved source, an unreachable source, the connection bound, row-level security on
  `analytics_sources`, the eval items) and 4 over HTTP (`test_analytics_sources_api.py`, PostgreSQL: admin routes,
  datasets and `/query`, and a streamed answer whose events and stored queries are checked).

Not measured: latency or throughput against a real deployment's database, large tables, TLS
`verify-full` against a real CA (the tests use `disable` on PostgreSQL and `require` on
MySQL), MariaDB, PostgreSQL before 18 or MySQL before 8.4, and execution accuracy with any
real model.

## Not built

- Connection pooling (each query connects anew) and per-source limits (the connection budget is
  per tenant).
- A web form to point an existing source elsewhere (the API does it).
- Queries across datasets: a query reads one dataset; the agent can combine results from
  several in its narrative, not in SQL.
- Column allow-lists, as for uploaded data: every column of a model table can be queried. Grant
  the database user only what people may see, or put a view in front.
- Other databases (SQL Server, Snowflake, BigQuery, ...). sqlglot reads their dialects; each
  would need its own read-only session and error handling in `remote.py`.
