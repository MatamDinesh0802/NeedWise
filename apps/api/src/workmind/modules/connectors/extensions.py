"""Where Enterprise plugs into connectors: permission sync (docs/permission-sync.md, ADR 0012).

Community: every synced document gets its source's collection's access (the collection-level
mapping, "this Drive folder -> Finance"). With the Enterprise feature "permission_sync",
workmind_ee implements `PermissionSync`: during a sync it reads who may see each item in the
source (Drive file permissions, Slack private channel members, GitHub collaborators and teams),
and the core writes that as the document's own access list. The list only narrows the
collection's access, and everything about enforcing it is in the core.

The core never imports workmind_ee: it calls the hook, and gets nothing in Community.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from workmind.core.extensions import Hook
from workmind.modules.connectors.base import Connector, RemoteItem
from workmind.modules.connectors.models import Source


@dataclass(frozen=True)
class ItemAcl:
    """Who the source lets read one item, as the source names them (`email:ana@acme.test`,
    `group:finance@acme.test`, `github-team:acme/platform`: see EXTERNAL_PATTERN).

    `restricted=False`: the source sets no limit narrower than the collection (a public Slack
    channel, a public repository), so the document keeps its collection's access.
    `error`: the item's permissions could not be read. The item is then restricted to nobody
    until a later sync reads them (fail closed), and the error is reported on the source."""

    principals: tuple[str, ...] = ()
    restricted: bool = True
    error: str | None = None


class PermissionSync(Protocol):
    def supports(self, source: Source) -> bool:
        """Whether this source's kind has permissions to sync (web pages have none)."""
        ...

    async def acls(
        self,
        source: Source,
        connector: Connector,
        scanned: Sequence[RemoteItem],
        known: Collection[str],
    ) -> Mapping[str, ItemAcl]:
        """ACLs by external id: for every scanned item, and for any already synced item
        (`known`) the answer also covers, e.g. older months of a Slack channel that an
        incremental scan does not list again. A scanned item left out gets an empty list:
        nobody sees it (fail closed). Raising fails the whole sync, which changes nothing."""
        ...


PERMISSION_SYNC = Hook[[], PermissionSync]("connectors.permission_sync")
