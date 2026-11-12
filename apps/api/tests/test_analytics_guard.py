"""The SQL guard against an attack corpus, and the row-policy rewrite run on the real Acme data.

Rejections are checked on the guard alone. Accepted queries that try to reach rows a sales rep
may not see (joins, subqueries, UNION, LATERAL, recursive CTEs, window functions, quoted and
qualified names) are executed on the Acme dataset as Dan, and must only ever return his rows.
"""

from pathlib import Path

import pytest

from tests.analytics_support import (
    DAN,
    DIEGO,
    acme_model,
    build_acme,
    csv_rows,
    dan_customer_ids,
    dan_filters,
    run,
)
from workmind.modules.analytics import engine, guard

TABLES = tuple(acme_model().tables)


def check(sql: str, *, filters: bool = True, max_rows: int = 100) -> guard.GuardedQuery:
    return guard.check(
        sql, tables=TABLES, row_filters=dan_filters() if filters else None, max_rows=max_rows
    )


@pytest.fixture(scope="module")
def acme(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_acme(tmp_path_factory.mktemp("analytics"))


# --- Rejected: (query, words the reason must contain) ----------------------------------------

STATEMENTS = [
    "DROP TABLE customers",
    "DELETE FROM customers",
    "UPDATE customers SET account_owner = 'dan@acme.test'",
    "INSERT INTO customers (customer_id) VALUES ('C999')",
    "CREATE TABLE stolen AS SELECT * FROM customers",
    "CREATE TEMP TABLE stolen AS SELECT * FROM customers",
    "CREATE VIEW v AS SELECT * FROM customers",
    "CREATE MACRO m(x) AS x",
    "ALTER TABLE customers RENAME TO c2",
    "TRUNCATE customers",
    "COPY customers TO '/tmp/out.csv'",
    "COPY (SELECT * FROM customers) TO '/tmp/out.parquet' (FORMAT parquet)",
    "ATTACH '/tmp/other.duckdb' AS other",
    "DETACH other",
    "PRAGMA database_list",
    "PRAGMA table_info('customers')",
    "SET enable_external_access = true",
    "SET threads TO 64",
    "RESET threads",
    "SET VARIABLE x = 1",
    "INSTALL httpfs",
    "LOAD httpfs",
    "LOAD '/tmp/evil.duckdb_extension'",
    "CALL pragma_version()",
    "CALL duckdb_settings()",
    "EXPORT DATABASE '/tmp/dump'",
    "IMPORT DATABASE '/tmp/dump'",
    "USE memory",
    "BEGIN TRANSACTION",
    "COMMIT",
    "ROLLBACK",
    "CHECKPOINT",
    "VACUUM",
    "SUMMARIZE customers",
    "SUMMARIZE SELECT * FROM customers",
    "DESCRIBE customers",
    "SHOW TABLES",
    "SHOW ALL TABLES",
    "EXPLAIN SELECT * FROM customers",
    "EXPLAIN ANALYZE SELECT * FROM customers",
    "PREPARE p AS SELECT * FROM customers",
    "EXECUTE p",
    "VALUES (1)",
    "TABLE customers",
    "PIVOT customers ON region USING count(*)",
    "UNPIVOT customers ON region INTO NAME k VALUE v",
]

NESTED_STATEMENTS = [
    "SELECT * FROM (SUMMARIZE customers)",
    "SELECT * FROM (DESCRIBE customers)",
    "SELECT * FROM (SHOW TABLES)",
    "WITH s AS (SUMMARIZE customers) SELECT * FROM s",
    "SELECT * FROM customers WHERE name IN (SUMMARIZE customers)",
    "SELECT * INTO stolen FROM customers",
    "SELECT * FROM customers PIVOT (count(*) FOR region IN ('DACH'))",
]

MULTIPLE = [
    "SELECT 1; SELECT 2",
    "SELECT * FROM products; DROP TABLE customers",
    "SELECT 1 /* */ ; DROP TABLE customers",
    "SELECT * FROM products;\n-- harmless?\nDELETE FROM customers",
    "SELECT * FROM products; SELECT * FROM customers",
]

EMPTY = ["", "   ", ";", "-- only a comment", "/* nothing */"]

INVALID = ["SELEC * FROM customers", "SELECT * FROM", "SELECT * FROM customers WHERE (", "'"]

TABLE_FUNCTIONS = [
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM read_csv_auto('/etc/passwd')",
    "SELECT * FROM read_csv(['/etc/passwd', '/etc/hosts'])",
    "SELECT * FROM READ_CSV('/etc/passwd')",
    "SELECT * FROM read_parquet('s3://bucket/x.parquet')",
    "SELECT * FROM read_json('/etc/passwd')",
    "SELECT * FROM read_text('/etc/hosts')",
    "SELECT * FROM read_blob('/etc/hosts')",
    "SELECT * FROM glob('/*')",
    "SELECT * FROM duckdb_settings()",
    "SELECT * FROM duckdb_tables()",
    "SELECT * FROM duckdb_secrets()",
    "SELECT * FROM range(10)",
    "SELECT * FROM generate_series(1, 10)",
    "SELECT * FROM query('SELECT * FROM customers')",
    "SELECT * FROM query('SELECT * FROM cust' || 'omers')",
    "SELECT * FROM query_table('customers')",
    "SELECT * FROM query_table(['cust' || 'omers'])",
    "SELECT * FROM sniff_csv('/etc/passwd')",
    "SELECT * FROM parquet_scan('x.parquet')",
    "SELECT * FROM parquet_metadata('x.parquet')",
    "SELECT * FROM postgres_scan('host=db', 'public', 'users')",
    "SELECT * FROM sqlite_scan('x.db', 't')",
    "SELECT * FROM mysql_query('db', 'SELECT 1')",
    "SELECT * FROM iceberg_scan('s3://x')",
    "SELECT * FROM delta_scan('s3://x')",
    "SELECT * FROM pragma_table_info('customers')",
    "SELECT * FROM customers JOIN read_csv('/etc/passwd') ON true",
    "SELECT * FROM customers, LATERAL range(3)",
    "SELECT * FROM customers WHERE name IN (SELECT * FROM read_text('/etc/hosts'))",
]

FILES_AS_TABLES = [
    "SELECT * FROM '/etc/passwd'",
    'SELECT * FROM "/etc/passwd"',
    "SELECT * FROM 'https://example.com/data.csv'",
    "SELECT * FROM 's3://bucket/data.parquet'",
    "SELECT * FROM 'customers.csv'",
    'SELECT * FROM "main.customers"',
]

FUNCTIONS = [
    "SELECT getenv('HOME')",
    "SELECT GETENV('HOME')",
    "SELECT \"getenv\"('HOME')",
    "SELECT current_setting('threads')",
    "SELECT read_text('/etc/hosts')",
    "SELECT '/etc/hosts'.read_text()",
    "SELECT which_secret('s3://bucket', 's3')",
    "SELECT json_execute_serialized_sql('x')",
    "SELECT http_get('https://example.com')",
    "SELECT httpfs_get('https://example.com')",
    "SELECT getvariable('x')",
    "SELECT setvariable('x', 1)",
    "SELECT nextval('s')",
    "SELECT system('ls')",
    "SELECT load('x')",
    "SELECT pg_typeof(1)",
    "SELECT duckdb_version()",
    "SELECT * FROM customers WHERE name = getenv('USER')",
    "SELECT name FROM customers ORDER BY current_setting('memory_limit')",
    "SELECT count(*) FILTER (WHERE getenv('X') = '1') FROM customers",
    "SELECT list_transform([1], x -> getenv('HOME'))",
]

OUTSIDE_THE_MODEL = [
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM information_schema.columns",
    "SELECT * FROM pg_catalog.pg_class",
    "SELECT * FROM pg_class",
    "SELECT * FROM pg_tables",
    "SELECT * FROM duckdb_tables",
    "SELECT * FROM duckdb_columns",
    "SELECT * FROM sqlite_master",
    "SELECT * FROM system.main.duckdb_tables",
    "SELECT * FROM temp.main.customers",
    "SELECT * FROM memory.main.customers",
    "SELECT * FROM acme.main.customers",
    "SELECT * FROM other.customers",
    "SELECT * FROM temp.customers",
    "SELECT * FROM salaries",
    "SELECT * FROM customers_backup",
    "SELECT * FROM customers c JOIN salaries s ON true",
    "SELECT (SELECT max(amount) FROM payroll) FROM products",
]

CTE_TRICKS = [
    "WITH customers AS (SELECT * FROM customers) SELECT * FROM customers",
    "WITH Customers AS (SELECT 1 AS x) SELECT * FROM products",
    'WITH "CUSTOMERS" AS (SELECT 1 AS x) SELECT * FROM products',
    "WITH invoice_lines AS (SELECT * FROM products) SELECT * FROM invoice_lines",
    "WITH RECURSIVE opportunities AS (SELECT 1 AS n) SELECT * FROM opportunities",
    "SELECT * FROM (WITH customers AS (SELECT 1) SELECT * FROM customers) x",
    # Scoping: these names resolve to real tables in DuckDB, not to the CTE.
    "SELECT * FROM (WITH secret AS (SELECT 1 AS x) SELECT * FROM secret) a, secret",
    "WITH a AS (SELECT * FROM secret), secret AS (SELECT 1 AS x) SELECT * FROM a",
    "WITH secret AS (SELECT * FROM secret) SELECT * FROM secret",
    "WITH a AS (SELECT 1 AS x) SELECT * FROM main.a",
]

TABLE_SYNTAX = [
    "SELECT * FROM customers TABLESAMPLE 50%",
    "SELECT * FROM customers AT (VERSION => 1)",
    "SELECT * FROM (customers JOIN invoice_lines USING (customer_id))",
]

PARAMETERS = [
    "SELECT * FROM customers WHERE name = ?",
    "SELECT * FROM customers WHERE name = $1",
    "SELECT * FROM customers WHERE name = $name",
]


@pytest.mark.parametrize(
    "sql",
    STATEMENTS
    + NESTED_STATEMENTS
    + MULTIPLE
    + EMPTY
    + INVALID
    + TABLE_FUNCTIONS
    + FILES_AS_TABLES
    + FUNCTIONS
    + OUTSIDE_THE_MODEL
    + CTE_TRICKS
    + TABLE_SYNTAX
    + PARAMETERS,
)
def test_rejected(sql: str) -> None:
    with pytest.raises(guard.QueryRejectedError) as raised:
        check(sql)
    assert raised.value.reason  # always a reason a person (or the model) can act on


@pytest.mark.parametrize(
    ("sql", "words"),
    [
        ("DROP TABLE customers", "read-only"),
        ("SELECT 1; SELECT 2", "exactly one statement"),
        ("", "empty"),
        ("SELEC 1", "not valid SQL"),
        ("SELECT * FROM read_csv('/etc/passwd')", "read_csv"),
        ("SELECT * FROM range(10)", "Table functions"),
        ("SELECT getenv('HOME')", "getenv"),
        ("SELECT * FROM information_schema.tables", "outside the data model"),
        ("SELECT * FROM salaries", "Tables you can query: customers, invoice_lines"),
        ("WITH customers AS (SELECT 1) SELECT 1", "CTE name"),
        ("SELECT * INTO x FROM customers", "INTO"),
        ("SELECT * FROM customers TABLESAMPLE 50%", "TABLESAMPLE"),
    ],
)
def test_reasons_say_what_to_fix(sql: str, words: str) -> None:
    with pytest.raises(guard.QueryRejectedError, match=words):
        check(sql)


def test_overlong_queries_are_rejected() -> None:
    with pytest.raises(guard.QueryRejectedError, match="too long"):
        check("SELECT 1" + " " * guard.MAX_SQL_CHARS)


# --- Accepted, and the rewrite ---------------------------------------------------------------


def test_filtered_tables_become_policy_subqueries_keeping_aliases() -> None:
    checked = check(
        "SELECT c.name, i.amount_eur FROM customers c JOIN invoice_lines i USING (customer_id)"
    )

    assert checked.sql == (
        "SELECT c.name, i.amount_eur FROM (SELECT * FROM customers WHERE "
        "(customers.account_owner = 'dan@acme.test')) AS c JOIN (SELECT * FROM invoice_lines "
        "WHERE (invoice_lines.customer_id IN (SELECT customer_id FROM customers WHERE "
        "account_owner = 'dan@acme.test'))) AS i USING (customer_id) LIMIT 101"
    )
    assert checked.tables == ("customers", "invoice_lines")
    assert checked.filtered == ("customers", "invoice_lines")
    assert checked.query == (
        "SELECT c.name, i.amount_eur FROM customers AS c JOIN invoice_lines AS i "
        "USING (customer_id)"
    )


def test_tables_without_a_filter_are_left_alone() -> None:
    checked = check("SELECT * FROM products")

    assert checked.sql == "SELECT * FROM products LIMIT 101"
    assert checked.filtered == ()


def test_without_filters_nothing_is_rewritten() -> None:
    checked = check("SELECT * FROM customers", filters=False)

    assert checked.sql == "SELECT * FROM customers LIMIT 101"


def test_the_policy_subquery_is_not_rewritten_again() -> None:
    # The invoice_lines policy reads customers: that inner reference stays the plain table.
    checked = check("SELECT count(*) FROM invoice_lines")

    assert checked.sql.count("FROM customers WHERE account_owner") == 1
    assert checked.sql.count("SELECT * FROM customers") == 0


@pytest.mark.parametrize(
    ("sql", "alias"),
    [
        ('SELECT * FROM "Customers"', '"Customers"'),
        ("SELECT * FROM CUSTOMERS", "CUSTOMERS"),
        ("SELECT * FROM main.customers", "customers"),
        ('SELECT * FROM "main"."customers"', '"customers"'),
        ("SELECT * FROM MAIN.Customers AS c", "c"),
    ],
)
def test_quoted_mixed_case_and_main_qualified_names_are_filtered(sql: str, alias: str) -> None:
    checked = check(sql)

    policy = "(SELECT * FROM customers WHERE (customers.account_owner = 'dan@acme.test'))"
    assert checked.sql == " ".join(["SELECT * FROM", policy, "AS", alias, "LIMIT 101"])


def test_user_values_in_policies_are_escaped_literals() -> None:
    from tests.analytics_support import SALES, context, resolved
    from workmind.modules.analytics.semantic import row_filters

    filters = row_filters(resolved(), context(SALES), "o'brien@acme.test' OR '1'='1")
    checked = guard.check("SELECT * FROM customers", tables=TABLES, row_filters=filters)

    assert "account_owner = 'o''brien@acme.test'' OR ''1''=''1'" in checked.sql


def test_comments_never_reach_the_executed_sql() -> None:
    checked = check("SELECT * FROM products -- */ ; DROP TABLE customers\n")

    assert checked.sql == "SELECT * FROM products LIMIT 101"
    assert "DROP" not in checked.sql


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT * FROM products", "SELECT * FROM products LIMIT 11"),
        ("SELECT * FROM products LIMIT 5", "SELECT * FROM products LIMIT 5"),
        ("SELECT * FROM products LIMIT 5000", "SELECT * FROM products LIMIT 11"),
        ("SELECT * FROM products LIMIT 3 OFFSET 2", "SELECT * FROM products LIMIT 3 OFFSET 2"),
        (
            "SELECT 1 AS x UNION ALL SELECT 2 ORDER BY 1",
            "SELECT 1 AS x UNION ALL SELECT 2 ORDER BY 1 LIMIT 11",
        ),
        (
            "SELECT * FROM products LIMIT 50 PERCENT",
            "SELECT * FROM (SELECT * FROM products LIMIT 50 PERCENT) AS _wm_result LIMIT 11",
        ),
        (
            "SELECT * FROM products LIMIT (SELECT 1000)",
            "SELECT * FROM (SELECT * FROM products LIMIT (SELECT 1000)) AS _wm_result LIMIT 11",
        ),
    ],
)
def test_row_cap_is_cap_plus_one(sql: str, expected: str) -> None:
    assert check(sql, max_rows=10).sql == expected


def test_cte_references_are_not_table_references() -> None:
    checked = check(
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 3), "
        "own AS (SELECT * FROM customers) SELECT * FROM n, own"
    )

    assert checked.tables == ("customers",)
    assert "FROM n," in checked.sql


# --- Executed as Dan on the Acme data: only his rows, whatever the query ---------------------

DAN_CUSTOMERS = dan_customer_ids()
DAN_LINES = sum(1 for r in csv_rows("invoice_lines") if r["customer_id"] in DAN_CUSTOMERS)
DAN_DEALS = sum(1 for r in csv_rows("opportunities") if r["owner"] == DAN)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT customer_id FROM customers",
        'SELECT customer_id FROM "Customers"',
        "SELECT customer_id FROM MAIN.CUSTOMERS",
        "FROM customers SELECT customer_id",
        "SELECT customer_id FROM customers "
        "WHERE account_owner = 'diego.alvarez@acme.test' OR 1 = 1",
        "SELECT customer_id FROM customers WHERE true OR account_owner <> 'dan@acme.test'",
        "SELECT customer_id FROM customers -- WHERE account_owner = 'dan@acme.test'\n",
        "SELECT customer_id FROM customers UNION SELECT customer_id FROM customers "
        "WHERE account_owner <> 'dan@acme.test'",
        "SELECT customer_id FROM invoice_lines UNION ALL SELECT customer_id FROM opportunities",
        "SELECT customer_id FROM customers INTERSECT SELECT customer_id FROM customers",
        "SELECT customer_id FROM (SELECT * FROM customers) AS invoice_lines",
        "SELECT i.customer_id FROM invoice_lines i JOIN customers c USING (customer_id)",
        "SELECT i.customer_id FROM invoice_lines i LEFT JOIN customers c USING (customer_id)",
        "SELECT c.customer_id FROM customers c RIGHT JOIN invoice_lines i USING (customer_id)",
        "SELECT c.customer_id FROM customers c FULL OUTER JOIN opportunities o USING (customer_id)",
        "SELECT customer_id FROM invoice_lines WHERE customer_id IN "
        "(SELECT customer_id FROM customers WHERE account_owner = 'diego.alvarez@acme.test')",
        "SELECT customer_id FROM invoice_lines i WHERE EXISTS "
        "(SELECT 1 FROM customers c WHERE c.customer_id = i.customer_id)",
        "SELECT customer_id FROM invoice_lines WHERE customer_id = ANY "
        "(SELECT customer_id FROM opportunities)",
        "SELECT c.customer_id FROM customers c, "
        "LATERAL (SELECT count(*) AS n FROM invoice_lines i WHERE i.customer_id = c.customer_id) x",
        "SELECT l.customer_id FROM products JOIN LATERAL "
        "(SELECT * FROM invoice_lines WHERE invoice_lines.product_id = products.product_id) l "
        "ON true",
        "WITH own AS (SELECT * FROM customers) SELECT customer_id FROM own",
        "WITH a AS (SELECT * FROM invoice_lines), b AS (SELECT * FROM a) SELECT customer_id FROM b",
        "WITH RECURSIVE ids(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM ids WHERE n < 30) "
        "SELECT c.customer_id FROM ids JOIN customers c "
        "ON c.customer_id = 'C' || lpad(n::VARCHAR, 3, '0')",
        "WITH RECURSIVE r(customer_id) AS (SELECT customer_id FROM customers "
        "UNION SELECT i.customer_id FROM invoice_lines i JOIN r ON true) "
        "SELECT customer_id FROM r",
        "SELECT DISTINCT first_value(customer_id) OVER (ORDER BY customer_id) AS customer_id "
        "FROM customers",
        "SELECT customer_id FROM "
        "(SELECT customer_id, row_number() OVER () AS n FROM invoice_lines)",
        "SELECT unnest(list(customer_id)) AS customer_id FROM customers",
        "SELECT (SELECT max(customer_id) FROM customers) AS customer_id",
        "SELECT customer_id FROM customers WHERE name = 'Bluefin' || ' Freight'",
        "SELECT customer_id FROM customers c1 CROSS JOIN products",
        "SELECT customer_id FROM opportunities NATURAL JOIN customers",
        "SELECT customer_id FROM customers USING SAMPLE 100 PERCENT (bernoulli)",
        "SELECT customer_id FROM customers ORDER BY customer_id LIMIT 1000",
        "SELECT customers.customer_id FROM customers",
        "SELECT customers.customer_id FROM main.customers",
        "(SELECT customer_id FROM customers)",
    ],
)
def test_dan_only_ever_sees_his_customers(acme: Path, sql: str) -> None:
    result = run(acme, sql, dan_filters())

    seen = {str(row[0]) for row in result.rows}
    assert seen <= DAN_CUSTOMERS, sql


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT count(*) FROM customers", len(DAN_CUSTOMERS)),
        ("SELECT count(*) FROM invoice_lines", DAN_LINES),
        ("SELECT count(*) FROM opportunities", DAN_DEALS),
        ("SELECT count(*) FROM products", 4),  # no row policy on products
        ("SELECT count(*) FROM customers c1 CROSS JOIN customers c2", len(DAN_CUSTOMERS) ** 2),
        ("SELECT count(*) FROM customers WHERE customer_id = 'C001'", 0),  # Bluefin is Diego's
        ("SELECT count(*) FROM opportunities WHERE owner = 'diego.alvarez@acme.test'", 0),
        ("SELECT max(n) FROM (SELECT count(*) OVER () AS n FROM invoice_lines)", DAN_LINES),
        (
            "SELECT count(*) FROM invoice_lines i JOIN customers c USING (customer_id) "
            "WHERE c.account_owner = 'diego.alvarez@acme.test'",
            0,
        ),
    ],
)
def test_counts_as_dan(acme: Path, sql: str, expected: int) -> None:
    assert run(acme, sql, dan_filters()).rows == [[expected]]


def test_error_oracles_do_not_reveal_hidden_rows(acme: Path) -> None:
    # If DuckDB evaluated these predicates on rows outside the policy, the query would fail.
    probes = [
        "SELECT count(*) FROM customers WHERE CASE WHEN customer_id = 'C001' "
        "THEN error('hidden row') ELSE true END",
        "SELECT count(*) FROM invoice_lines WHERE CASE WHEN customer_id = 'C001' "
        "THEN error('hidden row') ELSE true END",
        "SELECT count(*) FROM opportunities WHERE CASE WHEN owner = 'diego.alvarez@acme.test' "
        "THEN error('hidden row') ELSE true END",
    ]
    for sql in probes:
        count = run(acme, sql, dan_filters()).rows[0][0]
        assert isinstance(count, int)
        assert count > 0


def test_conversion_errors_only_show_values_dan_may_see(acme: Path) -> None:
    hidden = {r["name"] for r in csv_rows("customers") if r["account_owner"] == DIEGO}

    with pytest.raises(engine.QueryFailedError) as failed:
        run(acme, "SELECT CAST(name AS INTEGER) FROM customers", dan_filters())

    assert not any(name in failed.value.message for name in hidden)


def test_everyone_else_sees_all_rows(acme: Path) -> None:
    assert run(acme, "SELECT count(*) FROM customers").rows == [[24]]
    assert run(acme, "SELECT sum(amount_eur) FROM invoice_lines WHERE status <> 'void'").rows == [
        [38_900_000]
    ]


def test_function_names_cover_every_spelling() -> None:
    import sqlglot
    from sqlglot import exp

    node = sqlglot.parse_one("SELECT * FROM range(3)", dialect="duckdb").find(exp.Func)
    assert node is not None
    assert {"range", "generate_series"} & guard.function_names(node)
