"""Database sources without a database (docs/analytics-sql-sources.md): the guard in the
PostgreSQL and MySQL dialects (bypass attempts refused, model tables rewritten to the database's,
row policies as CTE fences), the compiler's dialects, the semantic layer's source tables, the
rules on hosts (SSRF), the password binding, the catalog and the error messages."""

import ipaddress
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import sqlglot
from pydantic import ValidationError
from sqlglot import exp

from workmind.core.context import RequestContext
from workmind.modules.analytics import guard, remote, semantic, sources
from workmind.modules.analytics.compiler import QueryFilter, SemanticQuery, compile_query
from workmind.modules.model_catalog.secrets import KeyRing, SecretsError

SHOP = Path(__file__).resolve().parents[3] / "seed" / "acme" / "analytics" / "sql" / "shop.yaml"
DIALECTS: list[guard.Dialect] = ["postgres", "mysql"]
KEY = "d29ya21pbmQtZGV2LW9ubHktTk9ULUEtU0VDUkVUISE="


def shop(dialect: guard.Dialect) -> semantic.SemanticModel:
    return semantic.parse(SHOP.read_text(), dialect)


def dan_filters(model: semantic.SemanticModel) -> dict[str, exp.Expr]:
    resolved = semantic.Resolved(
        model=model,
        access=frozenset({"dept:sales"}),
        policies=tuple(
            semantic.ResolvedPolicy(policy=p, applies=frozenset({"dept:sales"}), unless=frozenset())
            for p in model.row_policies
        ),
        unresolved=(),
    )
    ctx = RequestContext(
        request_id="t",
        tenant_id=uuid.UUID(int=1),
        user_id=uuid.UUID(int=2),
        org_role="member",
        principals=frozenset({"dept:sales"}),
        auth_method="session",
    )
    return semantic.row_filters(resolved, ctx, "dan@acme.test")


def check(
    sql: str, dialect: guard.Dialect, *, as_dan: bool = False, max_rows: int = 100
) -> guard.GuardedQuery:
    model = shop(dialect)
    return guard.check(
        sql,
        tables=model.tables.keys(),
        row_filters=dan_filters(model) if as_dan else None,
        max_rows=max_rows,
        dialect=dialect,
        relations=model.relations(),
    )


# --- Bypass attempts: every one refused, in both dialects ------------------------------------

ATTACKS = [
    # writes and other statements
    "DELETE FROM orders",
    "UPDATE orders SET amount_eur = 0",
    "INSERT INTO orders (order_id) VALUES ('x')",
    "DROP TABLE orders",
    "TRUNCATE orders",
    "CREATE TABLE x AS SELECT * FROM orders",
    "ALTER TABLE orders ADD COLUMN x INT",
    "GRANT SELECT ON orders TO PUBLIC",
    "SET statement_timeout = 0",
    "COPY orders TO STDOUT",
    "CALL p()",
    "EXPLAIN SELECT * FROM orders",
    "PREPARE p AS SELECT 1",
    "VACUUM",
    "BEGIN",
    "COMMIT",
    # several statements, also behind comments
    "SELECT 1; DELETE FROM orders",
    "SELECT 1; -- harmless\nDROP TABLE customers",
    "SELECT 1 /* ; */; SELECT 2",
    # functions with side effects, or that reach outside the query
    "SELECT pg_sleep(10)",
    "SELECT pg_sleep_for('5 minutes')",
    "SELECT sleep(10)",
    "SELECT benchmark(100000000, md5('x'))",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT load_file('/etc/passwd')",
    "SELECT lo_import('/etc/passwd')",
    "SELECT dblink('host=10.0.0.1', 'DELETE FROM t')",
    "SELECT * FROM dblink('host=x', 'SELECT 1') AS t(a int)",
    "SELECT query_to_xml('DELETE FROM shop.orders', true, true, '')",
    "SELECT set_config('default_transaction_read_only', 'off', false)",
    "SELECT current_setting('data_directory')",
    "SELECT nextval('shop.orders_seq')",
    "SELECT txid_current()",
    "SELECT get_lock('x', 10)",
    "SELECT ts_stat('SELECT 1')",
    "SELECT current_user",
    "SELECT public.do_something(1)",
    "SELECT shop.f(amount_eur) FROM orders",
    "SELECT my_function(amount_eur) FROM orders",
    # files, other schemas, catalogs, tables outside the model
    "SELECT * FROM shop.orders",
    "SELECT * FROM hr.salaries",
    "SELECT * FROM internal_notes",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM pg_catalog.pg_user",
    "SELECT * FROM pg_user",
    "SELECT * FROM mysql.user",
    "SELECT * FROM generate_series(1, 10)",
    # CTE tricks
    "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
    "WITH orders AS (SELECT * FROM customers) SELECT * FROM orders",
    "WITH _wm_rows_orders AS (SELECT * FROM orders) SELECT * FROM _wm_rows_orders",
    "WITH RECURSIVE salaries AS (SELECT * FROM salaries) SELECT * FROM salaries",
    "WITH x AS (SELECT * FROM y), y AS (SELECT 1) SELECT * FROM x",
    # locks, INTO, variables, parameters
    "SELECT * FROM orders FOR UPDATE",
    "SELECT * INTO new_table FROM orders",
]
MYSQL_ATTACKS = [
    "SELECT * FROM orders INTO OUTFILE '/tmp/x'",
    "SELECT * FROM orders INTO DUMPFILE '/tmp/x'",
    "LOAD DATA INFILE '/etc/passwd' INTO TABLE orders",
    "LOAD DATA LOCAL INFILE '/etc/passwd' INTO TABLE orders",
    "SELECT @@version",
    "SELECT @@hostname",
    "SELECT @x := 1",
    "SELECT * FROM orders LOCK IN SHARE MODE",
    "HANDLER orders OPEN",
    "DO SLEEP(5)",
    "SHOW TABLES",
    "SELECT ?",
]
POSTGRES_ATTACKS = [
    "DO $$ BEGIN DELETE FROM shop.orders; END $$",
    "SELECT $1",
    "LISTEN x",
    "NOTIFY x",
    "TABLE orders",
    "SELECT * FROM ONLY orders",
    "SELECT * FROM orders TABLESAMPLE SYSTEM (10)",
    # catalog names without a catalog table: reg* output functions read pg_class, pg_authid...
    "SELECT CAST(CAST(GENERATE_SERIES(1, 5000) AS OID) AS REGCLASS)",
    "SELECT CAST(order_id AS OID)::regclass FROM orders",
    "SELECT CAST(10 AS REGROLE)",
    "SELECT CAST(2200 AS REGNAMESPACE)",
    "SELECT CAST(1 AS REGPROCEDURE)",
    "SELECT 23::regtype",
    "SELECT version()",
]


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("sql", ATTACKS)
def test_bypass_attempts_are_refused(dialect: guard.Dialect, sql: str) -> None:
    with pytest.raises(guard.QueryRejectedError):
        check(sql, dialect)
    with pytest.raises(guard.QueryRejectedError):
        check(sql, dialect, as_dan=True)


@pytest.mark.parametrize("sql", MYSQL_ATTACKS)
def test_mysql_bypass_attempts_are_refused(sql: str) -> None:
    with pytest.raises(guard.QueryRejectedError):
        check(sql, "mysql")


@pytest.mark.parametrize("sql", POSTGRES_ATTACKS)
def test_postgres_bypass_attempts_are_refused(sql: str) -> None:
    with pytest.raises(guard.QueryRejectedError):
        check(sql, "postgres")


@pytest.mark.parametrize("dialect", DIALECTS)
def test_comments_never_reach_the_database(dialect: guard.Dialect) -> None:
    # MySQL runs the inside of /*! ... */: the SQL that runs is rebuilt without any comment.
    for sql in (
        "SELECT 1 /*! , SLEEP(5) */ FROM orders",
        "SELECT /*!50000 SLEEP(5), */ count(*) FROM orders",
        "SELECT count(*) -- ; DROP TABLE orders\nFROM orders",
    ):
        checked = check(sql, dialect)
        assert "/*" not in checked.sql, sql
        assert "--" not in checked.sql, sql
        assert "SLEEP" not in checked.sql.upper()
        assert "DROP" not in checked.sql.upper()


@pytest.mark.parametrize("dialect", DIALECTS)
def test_model_tables_become_the_databases_tables(dialect: guard.Dialect) -> None:
    checked = check(
        "SELECT c.region, sum(o.amount_eur) FROM orders o JOIN customers c "
        "ON c.customer_id = o.customer_id GROUP BY 1",
        dialect,
    )
    quote = '"' if dialect == "postgres" else "`"
    assert f"{quote}shop{quote}.{quote}orders{quote} AS o" in checked.sql
    assert f"{quote}shop{quote}.{quote}customers{quote} AS c" in checked.sql
    assert checked.query.startswith("SELECT c.region")  # what is shown keeps the model names
    assert "shop" not in checked.query
    assert checked.sql.endswith("LIMIT 101")
    assert checked.fences == ()  # the fences are inside the query
    assert checked.statements == checked.sql


@pytest.mark.parametrize("dialect", DIALECTS)
def test_row_policies_are_fences_at_the_head_of_the_query(dialect: guard.Dialect) -> None:
    checked = check(
        "WITH totals AS (SELECT customer_id, sum(amount_eur) AS total FROM orders GROUP BY 1) "
        "SELECT c.name, t.total FROM totals t JOIN customers c USING (customer_id) "
        "UNION ALL SELECT name, NULL FROM customers WHERE customer_id IN "
        "(SELECT customer_id FROM orders WHERE amount_eur > 100)",
        dialect,
        as_dan=True,
    )
    tree = sqlglot.parse_one(checked.sql, dialect=dialect)
    with_ = tree.args["with_"]
    names = [cte.alias for cte in with_.expressions]
    assert names[:2] == ["_wm_rows_customers", "_wm_rows_orders"]  # first, before the query's
    assert names[2] == "totals"
    materialized = [cte.args.get("materialized") for cte in with_.expressions[:2]]
    assert materialized == ([True, True] if dialect == "postgres" else [None, None])
    # Every read of a restricted table outside the fences goes through a fence.
    for table in tree.find_all(exp.Table):
        if table.db == "shop":
            assert guard._inside_fence(table), table.sql(dialect=dialect)
    fence = with_.expressions[0].this.sql(dialect=dialect)
    assert "'dan@acme.test'" in fence
    assert checked.filtered == ("customers", "orders")


@pytest.mark.parametrize("dialect", DIALECTS)
def test_the_users_values_are_literals_whatever_they_contain(dialect: guard.Dialect) -> None:
    model = shop(dialect)
    nasty = "x\\' OR 1=1 -- ' ; DROP TABLE orders"
    compiled = compile_query(
        model,
        SemanticQuery(
            metrics=["shop_revenue"],
            dimensions=["region"],
            filters=[QueryFilter(dimension="customer", values=[nasty])],
        ),
    )
    checked = guard.check(
        compiled.sql,
        tables=model.tables.keys(),
        max_rows=10,
        dialect=dialect,
        relations=model.relations(),
    )
    literals = [
        lit.this
        for lit in sqlglot.parse_one(checked.sql, dialect=dialect).find_all(exp.Literal)
        if lit.is_string
    ]
    assert nasty in literals  # parsed back by the database's rules: one string, unchanged


def test_the_compiler_writes_each_dialect() -> None:
    query = SemanticQuery(
        metrics=["shop_revenue", "orders_count", "customers_ordering"],
        dimensions=["quarter", "region"],
        date_from=date(2026, 1, 1),
    )
    postgres = compile_query(shop("postgres"), query).sql
    mysql = compile_query(shop("mysql"), query).sql
    assert "DATE_TRUNC('QUARTER', orders.order_date)" in postgres
    assert "FILTER(WHERE orders.status <> 'refunded')" in postgres
    assert "FILTER" not in mysql
    assert "DATE_TRUNC" not in mysql
    assert "MAKEDATE(YEAR(orders.order_date), 1) + INTERVAL (QUARTER(" in mysql
    assert "SUM(CASE WHEN orders.status <> 'refunded' THEN orders.amount_eur END)" in mysql
    assert "COUNT(DISTINCT CASE WHEN orders.status <> 'refunded' THEN orders.customer_id END)" in (
        mysql
    )
    for grain in ("day", "week", "month", "year"):
        sql = compile_query(
            shop("mysql"), SemanticQuery(metrics=["orders_count"], dimensions=[grain])
        ).sql
        # Each passes the guard (MAKEDATE and WEEKDAY are on MySQL's allow-list).
        model = shop("mysql")
        guard.check(sql, tables=model.tables.keys(), dialect="mysql", relations=model.relations())


# --- The semantic layer ---------------------------------------------------------------------


def test_database_models_name_their_tables() -> None:
    text = SHOP.read_text()
    assert shop("postgres").relations() == {
        "customers": ("shop", "customers"),
        "orders": ("shop", "orders"),
    }
    with pytest.raises(semantic.ModelError, match="only for database sources"):
        semantic.parse(text)  # the uploaded files' model has no source_table
    without = text.replace("    source_table: shop.orders\n", "")
    with pytest.raises(semantic.ModelError, match="add source_table"):
        semantic.parse(without, "postgres")
    with pytest.raises(semantic.ModelError, match=r"schema\.table"):
        semantic.parse(text.replace("shop.orders", "orders; DROP"), "postgres")
    with pytest.raises(semantic.ModelError, match="dialect"):
        semantic.parse("dialect: duckdb\n" + text, "postgres")
    twice = text.replace("source_table: shop.customers", "source_table: shop.orders")
    with pytest.raises(semantic.ModelError, match="same source_table"):
        semantic.parse(twice, "postgres")


@pytest.mark.parametrize("dialect", DIALECTS)
def test_database_models_refuse_unknown_functions(dialect: guard.Dialect) -> None:
    text = SHOP.read_text()
    with pytest.raises(semantic.ModelError, match="not available"):
        semantic.parse(
            text.replace("sql: sum(amount_eur)", "sql: sum(my_udf(amount_eur))", 1), dialect
        )
    with pytest.raises(semantic.ModelError, match=r"not available|not allowed"):
        semantic.parse(
            text.replace("sql: customers.region", "sql: pg_sleep(customers.region)"), dialect
        )


# --- Where WorkMind may connect -------------------------------------------------------------

PRIVATE = (ipaddress.ip_network("10.20.0.0/16"), ipaddress.ip_network("127.0.0.0/8"))


@pytest.mark.parametrize(
    "host",
    [
        "169.254.169.254",
        "metadata.google.internal",
        "169.254.10.1",
        "fe80::1",
        "::ffff:169.254.169.254",
        "fd00:ec2::254",
        "100.100.100.200",
        "0.0.0.0",
        "224.0.0.1",
    ],
)
def test_metadata_and_link_local_hosts_are_never_allowed(host: str) -> None:
    with pytest.raises(sources.SourceHostError):
        sources.check_host(host, PRIVATE)


@pytest.mark.parametrize("host", ["10.20.1.5", "127.0.0.1", "192.168.1.10", "172.16.0.3", "::1"])
def test_private_hosts_need_an_operator_setting(host: str) -> None:
    with pytest.raises(sources.SourceHostError, match="ANALYTICS_SOURCE_PRIVATE_NETWORKS"):
        sources.check_host(host, ())
    allowed = host in ("10.20.1.5", "127.0.0.1")
    if allowed:
        sources.check_host(host, PRIVATE)
    else:
        with pytest.raises(sources.SourceHostError):
            sources.check_host(host, PRIVATE)


def test_public_hosts_are_allowed() -> None:
    sources.check_host("8.8.8.8", ())
    sources.check_host("db.example.com", ())  # a name is checked when it resolves


async def test_names_are_checked_by_what_they_resolve_to() -> None:
    with pytest.raises(sources.SourceHostError, match="localhost resolves to"):
        await sources.resolve_host("localhost", 5432, ())
    addresses = await sources.resolve_host(
        "localhost", 5432, (*PRIVATE, ipaddress.ip_network("::1/128"))
    )
    assert "127.0.0.1" in addresses or "::1" in addresses
    with pytest.raises(sources.SourceHostError, match="does not resolve"):
        await sources.resolve_host("no-such-host.invalid", 5432, ())


def test_what_an_admin_gives_is_checked() -> None:
    base: dict[str, Any] = {
        "key": "shop",
        "name": "Shop",
        "kind": "postgres",
        "host": "db.example.com",
        "database": "shop",
        "username": "reader",
        "password": "s3cret-pw",
        "schemas": ["shop"],
    }
    assert sources.SourceIn.model_validate(base).port == 5432
    assert sources.SourceIn.model_validate({**base, "kind": "mysql"}).port == 3306
    assert sources.SourceIn.model_validate(base).tls_mode == "verify-full"
    for change in (
        {"key": "uploads"},
        {"key": "Shop DB"},
        {"host": "postgres://db.example.com"},
        {"host": "db.example.com/x"},
        {"host": "user@db.example.com"},
        {"schemas": []},
        {"schemas": ["shop; DROP"]},
        {"schemas": ["shop", "shop"]},
        {"port": 70000},
        {"tls_mode": "prefer"},
        {"tls_ca": "not a certificate"},
        {"password": ""},
        {"extra": 1},
    ):
        with pytest.raises(ValidationError):
            sources.SourceIn.model_validate({**base, **change})
    assert "s3cret-pw" not in repr(sources.SourceIn.model_validate(base))  # the password is secret


def test_a_password_only_opens_for_its_source_and_place() -> None:
    keyring = KeyRing(KEY)
    tenant, source = uuid.uuid4(), uuid.uuid4()
    place = ("postgres", "db.example.com", 5432, "shop", "reader")
    sealed = keyring.seal("s3cret", context=sources.password_context(tenant, source, *place))
    assert keyring.open(sealed, context=sources.password_context(tenant, source, *place)) == (
        "s3cret"
    )
    for other in (
        sources.password_context(uuid.uuid4(), source, *place),
        sources.password_context(tenant, uuid.uuid4(), *place),
        sources.password_context(
            tenant, source, "postgres", "evil.example", 5432, "shop", "reader"
        ),
        sources.password_context(
            tenant, source, "postgres", "db.example.com", 5433, "shop", "reader"
        ),
        sources.password_context(
            tenant, source, "postgres", "db.example.com", 5432, "shop", "admin"
        ),
    ):
        with pytest.raises(SecretsError):
            keyring.open(sealed, context=other)
    target = remote.Target(
        kind="postgres",
        address="10.0.0.1",
        host="db",
        port=5432,
        database="shop",
        user="reader",
        password="s3cret",
        tls_mode="require",
    )
    assert "s3cret" not in repr(target)


# --- The catalog ----------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["postgres", "mysql"])
def test_the_catalog_reads_information_schema_for_the_schemas_only(kind: remote.SourceKind) -> None:
    sql = sources.catalog_sql(kind, ["shop", "sales_2026"])
    tree = sqlglot.parse_one(sql, dialect=kind)
    assert {t.db for t in tree.find_all(exp.Table)} == {"information_schema"}
    assert "'shop', 'sales_2026'" in sql
    rows: list[list[object]] = [
        ["shop", "orders", "BASE TABLE", "order_id", "character varying"],
        ["shop", "orders", "BASE TABLE", "amount_eur", "numeric"],
        ["shop", "orders", "BASE TABLE", "OrderDate", "date"],
        ["shop", "v_orders", "VIEW", "n", "bigint"],
    ]
    tables = sources.catalog_tables(rows)
    assert [(t.schema_name, t.name, t.kind) for t in tables] == [
        ("shop", "orders", "table"),
        ("shop", "v_orders", "view"),
    ]
    assert [(c.name, c.model_type, c.usable) for c in tables[0].columns] == [
        ("order_id", "text", True),
        ("amount_eur", "decimal", True),
        ("OrderDate", "date", False),
    ]
    assert tables[0].model_dump(by_alias=True)["schema"] == "shop"


@pytest.mark.parametrize(
    ("data_type", "expected"),
    [
        ("integer", "integer"),
        ("bigint", "integer"),
        ("int", "integer"),
        ("numeric", "decimal"),
        ("double precision", "decimal"),
        ("decimal", "decimal"),
        ("date", "date"),
        ("timestamp without time zone", "timestamp"),
        ("datetime", "timestamp"),
        ("boolean", "boolean"),
        ("character varying", "text"),
        ("uuid", "text"),
        ("json", "text"),
    ],
)
def test_database_types_map_to_model_types(data_type: str, expected: str) -> None:
    assert sources.model_type(data_type) == expected


# --- Error messages -------------------------------------------------------------------------


class _PgError(Exception):
    def __init__(self, sqlstate: str, message: str) -> None:
        super().__init__(message)
        self.sqlstate, self.message = sqlstate, message


def test_errors_about_values_never_quote_them() -> None:
    value = _PgError("22P02", 'invalid input syntax for type integer: "Birch Supply Co"')
    assert "Birch" not in remote._pg_message(value)
    query = _PgError("42703", 'column "nope" does not exist')
    assert remote._pg_message(query) == 'Database error: column "nope" does not exist'
    assert remote._pg_message(_PgError("25006", "cannot execute DELETE")) == (
        remote._READ_ONLY_MESSAGE
    )
    mysql_value = Exception(1292, "Truncated incorrect DOUBLE value: 'Birch Supply Co'")
    assert "Birch" not in remote._mysql_message(mysql_value)
    mysql_json = Exception(3141, 'Invalid JSON text in argument 1: "Birch"')
    assert "Birch" not in remote._mysql_message(mysql_json)
    unknown = Exception(1999, "something about 'Birch Supply Co'")
    assert "Birch" not in remote._mysql_message(unknown)
    assert "Unknown column" in remote._mysql_message(Exception(1054, "Unknown column 'x'"))
