"""Permission sync: each synced document gets its source's access list (Enterprise feature
"permission_sync"; docs/permission-sync.md, ADR 0012).

The core writes and enforces document access lists (Community); this module reads them from
the sources, through the core's `PERMISSION_SYNC` hook, during every sync:

- Google Drive: each file's permissions. People (`email:`), groups (`group:`), domains
  (`domain:`) and "anyone with the link" (`anyone`), whatever their role: every role can read.
- Slack: a public channel is open to the whole workspace, so its documents keep the
  collection's access. A private channel's documents are limited to its members (`email:`
  when Slack gives the address, else `slack-user:<id>`). The list applies to every month of
  the channel, including months the incremental scan does not list again.
- GitHub: a public repository keeps the collection's access. A private one is limited to its
  collaborators (`github-user:<login>`) and teams (`github-team:<org>/<slug>`); an internal one
  also to its organization (`github-org:<org>`).

Fail closed: an item whose permissions the source refuses to show (no access to the file's
sharing, a private channel the bot left, collaborators the token may not list) is limited to
nobody until a later sync can read them, and the error is reported on the source. An outage or
a rate limit fails the whole sync instead, which changes nothing and is retried next interval.

Revocation without waiting for the next sync: every minute, `apply_drive_changes` reads the
Drive changes feed of every Drive source and applies the current permissions of the changed
files at once. A file that was removed or trashed is limited to nobody until the next full sync
deletes it. Slack and GitHub changes apply at the next sync of their source.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from typing import Any, Final

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from workmind.api.v1.sources import sync_deps_from
from workmind.core.resources import Resources
from workmind.core.tenancy import global_transaction, tenant_key, tenant_transaction
from workmind.modules.connectors.base import (
    Connector,
    ConnectorError,
    ConnectorRefusedError,
    RemoteItem,
)
from workmind.modules.connectors.extensions import ItemAcl
from workmind.modules.connectors.gdrive import GoogleDriveConnector
from workmind.modules.connectors.github import GitHubConnector
from workmind.modules.connectors.models import Source
from workmind.modules.connectors.slack import SlackConnector
from workmind.modules.connectors.sync import SyncDeps, apply_acls
from workmind.modules.identity.models import Tenant
from workmind.modules.knowledge.models import Document

FEATURE: Final = "permission_sync"
TASK_NAME: Final = "workmind_ee.permission_sync.drive_changes"
CHANGES_EVERY_S: Final = 60.0
CONCURRENCY: Final = 8  # parallel permission reads per source
MAX_PAGES: Final = 50  # of 100 collaborators, teams or members, or of Drive changes
PERMISSION_FIELDS: Final = "nextPageToken,permissions(type,emailAddress,domain,role,deleted)"
CHANGE_FIELDS: Final = "nextPageToken,newStartPageToken,changes(fileId,removed,file(trashed))"

log = structlog.get_logger("workmind_ee.permission_sync")


class SourcePermissions:
    """The core's PermissionSync, for Drive, Slack and GitHub sources."""

    def supports(self, source: Source) -> bool:
        return source.kind in ("gdrive", "slack", "github")

    async def acls(
        self,
        source: Source,
        connector: Connector,
        scanned: Sequence[RemoteItem],
        known: Collection[str],
    ) -> Mapping[str, ItemAcl]:
        if isinstance(connector, GoogleDriveConnector):
            return await drive_acls(connector, [i.external_id for i in scanned])
        if isinstance(connector, SlackConnector):
            return await slack_acls(connector, [*(i.external_id for i in scanned), *known])
        if isinstance(connector, GitHubConnector):
            return await github_acls(connector, [*(i.external_id for i in scanned), *known])
        return {}


def permission_sync() -> SourcePermissions:
    """The hook implementation (registered in workmind_ee.setup)."""
    return SourcePermissions()


async def _bounded[T](jobs: Iterable[Callable[[], Awaitable[T]]]) -> list[T]:
    gate = asyncio.Semaphore(CONCURRENCY)

    async def run(job: Callable[[], Awaitable[T]]) -> T:
        async with gate:
            return await job()

    return list(await asyncio.gather(*(run(job) for job in jobs)))


# --- Google Drive -------------------------------------------------------------------------------


def drive_principal(permission: Mapping[str, Any]) -> str | None:
    """One Drive permission as a source principal; None for one that grants nobody now."""
    if permission.get("deleted"):
        return None  # the account was deleted
    kind = permission.get("type")
    email = str(permission.get("emailAddress") or "").strip().lower()
    if kind == "user" and email:
        return f"email:{email}"
    if kind == "group" and email:
        return f"group:{email}"
    if kind == "domain" and permission.get("domain"):
        return f"domain:{str(permission['domain']).strip().lower()}"
    if kind == "anyone":
        return "anyone"
    return None


async def drive_file_acl(connector: GoogleDriveConnector, file_id: str) -> ItemAcl:
    principals: set[str] = set()
    token = ""
    try:
        for _ in range(MAX_PAGES):
            params = {"fields": PERMISSION_FIELDS, "pageSize": "100", "supportsAllDrives": "true"}
            if token:
                params["pageToken"] = token
            page = await connector.get_json(f"/files/{file_id}/permissions", **params)
            principals |= {
                p
                for permission in page.get("permissions", [])
                if (p := drive_principal(permission))
            }
            token = str(page.get("nextPageToken") or "")
            if not token:
                break
        else:
            return ItemAcl(error="too many permissions to read")
    except ConnectorRefusedError as exc:
        return ItemAcl(error=str(exc))
    return ItemAcl(principals=tuple(sorted(principals)))


async def drive_acls(
    connector: GoogleDriveConnector, file_ids: Sequence[str]
) -> dict[str, ItemAcl]:
    ids = sorted(set(file_ids))
    found = await _bounded(
        [lambda file_id=file_id: drive_file_acl(connector, file_id) for file_id in ids]
    )
    return dict(zip(ids, found, strict=True))


# --- Slack --------------------------------------------------------------------------------------


def slack_channel(external_id: str) -> str:
    return external_id.split(":", 1)[0]  # "<channel id>:<month>"


async def _slack_pages(
    connector: SlackConnector, method: str, key: str, **params: str
) -> list[Any]:
    found: list[Any] = []
    cursor = ""
    for _ in range(MAX_PAGES):
        page = await connector.api(method, **params, **({"cursor": cursor} if cursor else {}))
        found += page.get(key, [])
        metadata: dict[str, Any] = page.get("response_metadata") or {}
        cursor = str(metadata.get("next_cursor") or "")
        if not cursor:
            return found
    raise ConnectorRefusedError(f"Slack {method}: more than {MAX_PAGES} pages")


async def slack_acls(connector: SlackConnector, external_ids: Sequence[str]) -> dict[str, ItemAcl]:
    by_channel: dict[str, list[str]] = {}
    for external_id in sorted(set(external_ids)):
        by_channel.setdefault(slack_channel(external_id), []).append(external_id)
    people: SlackPeople | None = None  # read once, and only for a private channel
    found: dict[str, ItemAcl] = {}
    for channel, items in by_channel.items():
        try:
            info = (await connector.api("conversations.info", channel=channel))["channel"]
            if not (info.get("is_private") or info.get("is_group") or info.get("is_mpim")):
                acl = ItemAcl(restricted=False)  # open to the workspace: the collection's access
            else:
                members = await _slack_pages(
                    connector, "conversations.members", "members", channel=channel, limit="200"
                )
                people = people or await slack_people(connector)
                acl = ItemAcl(
                    principals=tuple(
                        sorted({p for m in members if (p := people.principal(str(m)))})
                    )
                )
        except ConnectorRefusedError as exc:
            acl = ItemAcl(error=str(exc))
        for external_id in items:
            found[external_id] = acl
    return found


class SlackPeople:
    """The workspace's members: e-mail addresses, and who is a bot or deleted."""

    def __init__(self, emails: Mapping[str, str], ignored: Collection[str]) -> None:
        self._emails = emails
        self._ignored = ignored

    def principal(self, member: str) -> str | None:
        """`email:` when Slack gives the address, else `slack-user:<id>` (which needs a
        mapping); None for bots and deleted accounts, which read nothing."""
        if member in self._ignored:
            return None
        email = self._emails.get(member)
        return f"email:{email}" if email else f"slack-user:{member.lower()}"


async def slack_people(connector: SlackConnector) -> SlackPeople:
    users = await _slack_pages(connector, "users.list", "members", limit="200")
    emails: dict[str, str] = {}
    ignored: set[str] = set()
    for user in users:
        member = str(user.get("id"))
        if user.get("deleted") or user.get("is_bot"):
            ignored.add(member)
            continue
        profile: dict[str, Any] = user.get("profile") or {}
        if email := str(profile.get("email") or "").strip().lower():
            emails[member] = email
    return SlackPeople(emails, ignored)


# --- GitHub -------------------------------------------------------------------------------------


async def _github_pages(
    connector: GitHubConnector, path: str, **params: str
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        batch: list[dict[str, Any]] = (
            await connector.get(path, **params, per_page="100", page=str(page))
        ).json()
        found += batch
        if len(batch) < 100:
            return found
    raise ConnectorRefusedError(f"{path}: more than {MAX_PAGES * 100} entries")


async def github_repo_acl(connector: GitHubConnector) -> ItemAcl:
    repo = connector.repo
    try:
        info: dict[str, Any] = (await connector.get(f"/repos/{repo}")).json()
        visibility = str(info.get("visibility") or ("private" if info.get("private") else "public"))
        if visibility == "public":
            return ItemAcl(restricted=False)  # anyone can read it: the collection's access
        owner = repo.split("/", 1)[0].lower()
        principals = {
            f"github-user:{str(c['login']).lower()}"
            for c in await _github_pages(
                connector, f"/repos/{repo}/collaborators", affiliation="all"
            )
            if c.get("login")
        }
        principals |= {
            f"github-team:{owner}/{str(t['slug']).lower()}"
            for t in await _github_pages(connector, f"/repos/{repo}/teams")
            if t.get("slug")
        }
        if visibility == "internal":
            principals.add(f"github-org:{owner}")
        return ItemAcl(principals=tuple(sorted(principals)))
    except ConnectorRefusedError as exc:
        return ItemAcl(error=str(exc))


async def github_acls(
    connector: GitHubConnector, external_ids: Sequence[str]
) -> dict[str, ItemAcl]:
    """The repository's access, for every file of it."""
    acl = await github_repo_acl(connector)
    return {external_id: acl for external_id in sorted(set(external_ids))}


# --- Immediate revocation: the Drive changes feed ------------------------------------------------


def _changes_key(source: Source) -> str:
    return tenant_key(source.tenant_id, "permsync", "drive", source.id)


async def drive_changes(deps: SyncDeps, valkey: Redis, source: Source) -> int:
    """Applies the current permissions of every file of this source that Drive reports as
    changed since the last call (the first call only records where the feed starts). Returns
    how many documents' access lists changed."""
    credentials = deps.secrets.open(source.id, source.credentials) if source.credentials else {}
    connector = deps.connectors(source, credentials)
    if not isinstance(connector, GoogleDriveConnector):
        return 0
    key = _changes_key(source)
    stored = await valkey.get(key)
    token = stored.decode() if isinstance(stored, bytes) else stored
    if not token:
        start = await connector.get_json("/changes/startPageToken", supportsAllDrives="true")
        await valkey.set(key, str(start["startPageToken"]))
        return 0
    changed: dict[str, bool] = {}  # file id -> gone (removed or trashed)
    for _ in range(MAX_PAGES):
        page = await connector.get_json(
            "/changes",
            pageToken=str(token),
            fields=CHANGE_FIELDS,
            pageSize="1000",
            supportsAllDrives="true",
            includeItemsFromAllDrives="true",
        )
        for change in page.get("changes", []):
            file: dict[str, Any] = change.get("file") or {}
            file_id = str(change.get("fileId") or "")
            if file_id:
                changed[file_id] = bool(change.get("removed") or file.get("trashed"))
        if page.get("newStartPageToken"):
            token = str(page["newStartPageToken"])
            break
        token = str(page.get("nextPageToken") or "")
        if not token:
            break
    async with tenant_transaction(deps.sessions, source.tenant_id) as session:
        ours = set(
            await session.scalars(
                select(Document.external_id).where(
                    Document.source_id == source.id, Document.external_id.in_(sorted(changed))
                )
            )
        )
    acls: dict[str, ItemAcl] = {
        file_id: ItemAcl() for file_id in ours if file_id and changed[file_id]
    }  # gone: nobody, until the next full sync deletes it
    acls |= await drive_acls(connector, [f for f in ours if f and not changed[f]])
    applied = await apply_acls(deps.sessions, deps.qdrant, deps.collection_name, source, acls)
    if token:
        await valkey.set(key, token)
    refreshed = connector.updated_credentials
    if refreshed is not None:
        async with tenant_transaction(deps.sessions, source.tenant_id) as session:
            stored_source = await session.get(Source, source.id)
            if stored_source is not None:
                stored_source.credentials = deps.secrets.seal(source.id, refreshed)
    if applied:
        log.info("drive_permissions_applied", source=source.key, documents=applied)
    return applied


async def apply_drive_changes(resources: Resources) -> dict[str, int]:
    """The scheduled task: every enabled Drive source of every active tenant. A source that
    fails is logged and skipped; the others still run."""
    if resources.settings is None:
        raise RuntimeError("permission sync needs the settings the resources came from")
    deps = sync_deps_from(resources, resources.settings)
    async with global_transaction(resources.db) as session:
        tenants = list(
            (await session.scalars(select(Tenant.id).where(Tenant.status == "active"))).all()
        )
    applied: dict[str, int] = {}
    for tenant_id in tenants:
        for source in await _drive_sources(deps, tenant_id):
            try:
                applied[f"{tenant_id}/{source.key}"] = await drive_changes(
                    deps, resources.valkey, source
                )
            except (ConnectorError, KeyError, ValueError) as exc:
                log.warning("drive_changes_failed", source=source.key, error=str(exc)[:200])
    return applied


async def _drive_sources(deps: SyncDeps, tenant_id: uuid.UUID) -> list[Source]:
    async with tenant_transaction(deps.sessions, tenant_id) as session:
        return list(
            (
                await session.scalars(
                    select(Source).where(
                        Source.kind == "gdrive",
                        Source.enabled.is_(True),
                        Source.credentials.is_not(None),
                    )
                )
            ).all()
        )
