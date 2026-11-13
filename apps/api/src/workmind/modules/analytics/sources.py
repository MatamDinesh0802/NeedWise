"""PostgreSQL and MySQL databases as analytics datasets (docs/analytics-sql-sources.md).

An org admin connects a database (host, port, database, user, password, TLS mode, and the
schemas its tables may come from), picks tables from what the connection can see, and writes a
semantic model for them like the one for uploaded files: tables (each naming its
`source_table`), joins, dimensions, metrics, who may query and row policies. Queries then take
the same path as uploaded data (service.py): the semantic compiler and the SQL guard in the
source's dialect, the person's row policies as fences, and a worker process that runs the SQL in
a read-only session (remote.py).

This module holds what is particular to databases:
- the settings an admin gives (SourceIn, SourcePatch), checked;
- where WorkMind may connect (server-side request forgery): never link-local addresses or cloud
  metadata endpoints; private and loopback addresses only inside the networks the operator
  lists in ANALYTICS_SOURCE_PRIVATE_NETWORKS; everything else (public) is allowed. A name is
  resolved before every connection and the connection goes to the address that was checked
  (with verify-full TLS it goes to the name, whose certificate must match);
- what binds an encrypted password to its source (password_context);
- the catalog (information_schema) and how its types map to the semantic model's.
"""

import asyncio
import ipaddress
import re
import socket
import uuid
from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from sqlglot import exp

from workmind.core.urls import METADATA_ADDRESSES, METADATA_HOSTS, RESOLVE_TIMEOUT_S
from workmind.modules.analytics.remote import SourceKind, TlsMode
from workmind.modules.analytics.semantic import ColumnType
from workmind.modules.identity.models import SLUG_PATTERN

UPLOADS = "uploads"  # the dataset of uploaded files: no source may take this key
DEFAULT_PORTS: dict[SourceKind, int] = {"postgres": 5432, "mysql": 3306}
SCHEMA_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")
HOST = re.compile(r"^[A-Za-z0-9.:\[\]_-]{1,253}$")
MAX_SCHEMAS = 20
MAX_CATALOG_COLUMNS = 20_000
Network = ipaddress.IPv4Network | ipaddress.IPv6Network
Address = ipaddress.IPv4Address | ipaddress.IPv6Address


class SourceHostError(ValueError):
    """The host may not be connected to; the message says why, for the admin."""


# --- What an admin gives ---------------------------------------------------------------------


Name = Annotated[str, Field(min_length=1, max_length=63)]


class _Fields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("host", check_fields=False)
    @classmethod
    def _host(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip().lower().rstrip(".")
        if not HOST.match(value):
            raise ValueError("a host name or an IP address, without a scheme, port or path")
        return value

    @field_validator("schemas", check_fields=False)
    @classmethod
    def _schemas(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        bad = [s for s in value if not SCHEMA_NAME.match(s)]
        if bad:
            raise ValueError(f"not schema names: {bad}")
        if len(set(value)) != len(value):
            raise ValueError("a schema is listed twice")
        return value

    @field_validator("tls_ca", check_fields=False)
    @classmethod
    def _tls_ca(cls, value: str | None) -> str | None:
        if value is not None and value.strip() and "BEGIN CERTIFICATE" not in value:
            raise ValueError("a PEM certificate (-----BEGIN CERTIFICATE-----)")
        return value.strip() or None if value is not None else None


class SourceIn(_Fields):
    key: str = Field(min_length=1, max_length=63, pattern=SLUG_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    kind: SourceKind
    host: str = Field(min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)  # default: the kind's usual port
    database: Name
    username: Name
    password: SecretStr = Field(min_length=1, max_length=1024)
    tls_mode: TlsMode = "verify-full"
    tls_ca: str | None = Field(default=None, max_length=20_000)
    schemas: list[str] = Field(min_length=1, max_length=MAX_SCHEMAS)
    enabled: bool = True

    @model_validator(mode="after")
    def _defaults(self) -> "SourceIn":
        if self.key == UPLOADS:
            raise ValueError(f"the key {UPLOADS!r} is the uploaded files' dataset")
        if self.port is None:
            self.port = DEFAULT_PORTS[self.kind]
        return self


class SourcePatch(_Fields):
    """A change; fields left out stay. Changing where it connects (host, port, database, user)
    needs the password again: the stored one is bound to the old place."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    host: str | None = Field(default=None, min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: Name | None = None
    username: Name | None = None
    password: SecretStr | None = Field(default=None, min_length=1, max_length=1024)
    tls_mode: TlsMode | None = None
    tls_ca: str | None = Field(default=None, max_length=20_000)
    schemas: list[str] | None = Field(default=None, min_length=1, max_length=MAX_SCHEMAS)
    enabled: bool | None = None


def password_context(
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    kind: str,
    host: str,
    port: int,
    database: str,
    username: str,
) -> bytes:
    """Associated data for a source's sealed password: a ciphertext copied to another tenant or
    source, or left behind when the source is pointed elsewhere, does not decrypt (an admin
    cannot send the stored password to a server of their choosing)."""
    parts = ["analytics-source", str(tenant_id), str(source_id), kind, host, str(port)]
    return "|".join([*parts, database, username]).encode()


# --- Where WorkMind may connect --------------------------------------------------------------


def check_address(address: Address, networks: Sequence[Network]) -> None:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address in METADATA_ADDRESSES:
        raise SourceHostError("cloud metadata endpoints are not allowed")
    if address.is_link_local:
        raise SourceHostError("link-local addresses are not allowed")
    if address.is_multicast or address.is_unspecified:
        raise SourceHostError("this address cannot be used")
    if address.is_global:
        return
    if not any(address.version == n.version and address in n for n in networks):
        raise SourceHostError(
            f"{address} is a private or loopback address: an operator must allow its network "
            "in ANALYTICS_SOURCE_PRIVATE_NETWORKS first"
        )


def check_host(host: str, networks: Sequence[Network]) -> None:
    """A name or a literal address, without resolving it."""
    host = host.strip().rstrip(".").lower()
    if not host:
        raise SourceHostError("the host is empty")
    if host in METADATA_HOSTS:
        raise SourceHostError("cloud metadata endpoints are not allowed")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return
    check_address(literal, networks)


async def resolve_host(host: str, port: int, networks: Sequence[Network]) -> list[str]:
    """Every address `host` resolves to now, each checked; the first is what to connect to.
    A name that does not resolve cannot be connected to."""
    check_host(host, networks)
    bare = host.strip().rstrip(".").strip("[]")
    try:
        ipaddress.ip_address(bare)
        return [bare]
    except ValueError:
        pass
    try:
        infos = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(bare, port, type=socket.SOCK_STREAM),
            RESOLVE_TIMEOUT_S,
        )
    except (OSError, TimeoutError) as exc:
        raise SourceHostError(f"{host} does not resolve") from exc
    addresses: list[str] = []
    for info in infos:
        text = str(info[4][0]).split("%")[0]
        try:
            check_address(ipaddress.ip_address(text), networks)
        except ValueError as exc:
            raise SourceHostError(f"{host} resolves to {text}: {exc}") from exc
        if text not in addresses:
            addresses.append(text)
    if not addresses:
        raise SourceHostError(f"{host} does not resolve")
    return addresses


# --- The catalog -----------------------------------------------------------------------------


class CatalogColumn(BaseModel):
    name: str
    type: str  # as the database names it, e.g. character varying, numeric, int
    model_type: ColumnType  # what the semantic model calls it
    # A lower-case identifier: other column names cannot be used in a model (rename them in a
    # view).
    usable: bool


class CatalogTable(BaseModel):
    schema_name: str = Field(serialization_alias="schema")
    name: str
    kind: Literal["table", "view"]
    columns: list[CatalogColumn]

    @property
    def source_table(self) -> str:
        return f"{self.schema_name}.{self.name}"


def catalog_sql(kind: SourceKind, schemas: Sequence[str]) -> str:
    """The tables and columns of these schemas the connection may see, from
    information_schema. Schema names are literals in the tree (validated names), never text
    spliced into SQL."""
    dialect = "postgres" if kind == "postgres" else "mysql"
    c, t = exp.to_identifier("c"), exp.to_identifier("t")
    names = [exp.Literal.string(s) for s in schemas]

    def column(table: exp.Identifier, name: str) -> exp.Column:
        return exp.column(name, table=table.copy())

    select = (
        exp.select(
            column(c, "table_schema"),
            column(c, "table_name"),
            column(t, "table_type"),
            column(c, "column_name"),
            column(c, "data_type"),
        )
        .from_(exp.to_table("information_schema.columns").as_("c"))
        .join(
            exp.to_table("information_schema.tables").as_("t"),
            on=exp.and_(
                exp.EQ(this=column(t, "table_schema"), expression=column(c, "table_schema")),
                exp.EQ(this=column(t, "table_name"), expression=column(c, "table_name")),
            ),
        )
        .where(exp.In(this=column(c, "table_schema"), expressions=names))
        .order_by(column(c, "table_schema"), column(c, "table_name"), column(c, "ordinal_position"))
        .limit(MAX_CATALOG_COLUMNS + 1)
    )
    return select.sql(dialect=dialect)


def catalog_tables(rows: Sequence[Sequence[object]]) -> list[CatalogTable]:
    tables: dict[tuple[str, str], CatalogTable] = {}
    for schema, table, table_type, name, data_type in rows:
        key = (str(schema), str(table))
        if key not in tables:
            kind: Literal["table", "view"] = (
                "view" if "VIEW" in str(table_type).upper() else "table"
            )
            tables[key] = CatalogTable(schema_name=key[0], name=key[1], kind=kind, columns=[])
        tables[key].columns.append(
            CatalogColumn(
                name=str(name),
                type=str(data_type),
                model_type=model_type(str(data_type)),
                usable=bool(re.match(r"^[a-z_][a-z0-9_]{0,62}$", str(name))),
            )
        )
    return list(tables.values())


def model_type(data_type: str) -> ColumnType:
    """information_schema's data_type -> the semantic model's column type."""
    t = data_type.lower()
    if t in ("date",):
        return "date"
    if t.startswith(("timestamp", "datetime")):
        return "timestamp"
    if t in ("boolean", "bool"):
        return "boolean"
    if t in ("smallint", "integer", "int", "bigint", "tinyint", "mediumint", "year") or (
        t.endswith("serial")
    ):
        return "integer"
    if t.startswith(("numeric", "decimal", "real", "double", "float", "money")):
        return "decimal"
    return "text"
