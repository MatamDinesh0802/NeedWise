"""Collection permissions in the two places they are enforced (ARCHITECTURE section 7.6):

- Lock 1: the `acl` payload on every chunk in Qdrant, matched against RequestContext principals.
- Lock 2: OpenFGA tuples, checked before an answer uses a document.

Both are derived from the same collection_grants rows, so they cannot disagree by design.
"""

import uuid
from collections.abc import Iterable

from workmind.modules.authz.openfga import RelationTuple
from workmind.modules.knowledge.models import Collection, CollectionGrant, Document

_USERSETS = {
    "user": "user:{}",
    "group": "group:{}#member",
    "dept": "department:{}#member",
    "dept-manager": "department:{}#manager",
    "org": "organization:{}#member",
}


def openfga_user(principal: str) -> str:
    kind, _, identifier = principal.partition(":")
    return _USERSETS[kind].format(identifier)


def acl_principals(collection: Collection, grants: Iterable[CollectionGrant]) -> list[str]:
    """Everyone who may view: viewers and editors (editing implies viewing) and the owner."""
    principals = {g.principal for g in grants}
    if collection.owner_id:
        principals.add(f"user:{collection.owner_id}")
    return sorted(principals)


def collection_tuples(
    tenant_id: uuid.UUID, collection: Collection, grants: Iterable[CollectionGrant]
) -> set[RelationTuple]:
    obj = f"collection:{collection.id}"
    tuples = {RelationTuple(f"organization:{tenant_id}", "organization", obj)}
    if collection.department_id:
        tuples.add(RelationTuple(f"department:{collection.department_id}", "department", obj))
    if collection.owner_id:
        tuples.add(RelationTuple(f"user:{collection.owner_id}", "owner", obj))
    tuples |= {RelationTuple(openfga_user(g.principal), g.relation, obj) for g in grants}
    return tuples


def document_tuple(document: Document) -> RelationTuple:
    return RelationTuple(
        f"collection:{document.collection_id}", "collection", f"document:{document.id}"
    )
