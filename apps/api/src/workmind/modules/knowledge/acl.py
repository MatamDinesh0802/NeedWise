"""Permissions in the two places they are enforced (ARCHITECTURE section 7.6, ADR 0012):

- Lock 1: payload on every chunk in Qdrant, matched against RequestContext principals:
  `acl` (who may view the collection) and, for a document with its own access list,
  `own_acl: true` plus `doc_acl` (who is on that list).
- Lock 2: OpenFGA tuples, checked before an answer uses a document.

Both are derived from the same collection_grants and document_grants rows, so they cannot
disagree by design. A document's own list only narrows: to see it, a principal must pass the
collection AND the list.
"""

import uuid
from collections.abc import Iterable
from typing import Any

from workmind.modules.authz.openfga import RelationTuple
from workmind.modules.knowledge.models import Collection, CollectionGrant, Document, DocumentGrant

_USERSETS = {
    "user": "user:{}",
    "group": "group:{}#member",
    "dept": "department:{}#member",
    "dept-manager": "department:{}#manager",
    "org": "organization:{}#member",
}
# The grantee of a document without its own access list: every user (still only those who can
# view the collection, by the model's intersection).
EVERYONE = "user:*"


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


def document_principals(grants: Iterable[DocumentGrant]) -> list[str]:
    """The principals on a document's own list. Unmapped entries (no principal) grant nothing."""
    return sorted({g.principal for g in grants if g.principal})


def document_tuples(document: Document, grants: Iterable[DocumentGrant] = ()) -> set[RelationTuple]:
    """Every tuple of a document: its collection, and its grantees (the wildcard without an own
    list; exactly the mapped principals with one, so an empty list leaves nobody)."""
    obj = f"document:{document.id}"
    tuples = {document_tuple(document)}
    if not document.own_acl:
        tuples.add(RelationTuple(EVERYONE, "grantee", obj))
    else:
        tuples |= {
            RelationTuple(openfga_user(p), "grantee", obj) for p in document_principals(grants)
        }
    return tuples


def document_payload(document: Document, grants: Iterable[DocumentGrant]) -> dict[str, Any]:
    """Lock 1 fields of every chunk of the document (Qdrant payload)."""
    return {
        "own_acl": bool(document.own_acl),
        "doc_acl": document_principals(grants) if document.own_acl else [],
    }


def can_view_document(
    principals: Iterable[str], document: Document, grants: Iterable[DocumentGrant]
) -> bool:
    """The document's own list, for someone who can view its collection (checked separately)."""
    if not document.own_acl:
        return True
    return bool(set(principals) & set(document_principals(grants)))
