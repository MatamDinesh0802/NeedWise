"""Guardrails for permission-aware retrieval (ARCHITECTURE 7.6, ADR 0006 and 0012).

Every path that reads chunks for a person (chat, /ask, /search, agents, research, MCP,
workflows, evaluations) goes through `Retriever.search`, which builds lock 1 itself and runs
lock 2 on what it found. These tests fail when code queries the index another way, or when
lock 1 stops narrowing by a document's own access list.
"""

import ast
import uuid
from pathlib import Path

from workmind.core.context import RequestContext
from workmind.modules.retrieval.service import Retriever

ROOT = Path(__file__).resolve().parents[3]
SOURCES = [ROOT / "apps" / "api" / "src", ROOT / "ee" / "python" / "src"]
RETRIEVER = Path("apps/api/src/workmind/modules/retrieval/service.py")
CLIENT = Path("apps/api/src/workmind/modules/retrieval/qdrant.py")


def _calls(attribute: str) -> list[Path]:
    """Files (relative to the repository) that call `<something>.<attribute>(...)`."""
    found: list[Path] = []
    for base in SOURCES:
        for path in base.rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            if any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == attribute
                and "qdrant" in ast.unparse(node.func.value).lower()
                for node in ast.walk(tree)
            ):
                found.append(path.relative_to(ROOT))
    return sorted(found)


def test_only_the_retriever_queries_the_index_for_people() -> None:
    assert _calls("query") == [RETRIEVER]


def test_only_the_qdrant_client_names_the_query_endpoint() -> None:
    users = [
        path.relative_to(ROOT)
        for base in SOURCES
        for path in base.rglob("*.py")
        if "points/query" in path.read_text() or "points/scroll" in path.read_text()
    ]
    assert users == [CLIENT]


def _filter_for(principals: frozenset[str]) -> dict[str, object]:
    retriever = Retriever(
        qdrant=None,  # type: ignore[arg-type]
        embeddings=None,  # type: ignore[arg-type]
        fga=None,
        sessions=None,  # type: ignore[arg-type]
        collection_name="chunks",
    )
    ctx = RequestContext(
        request_id="t",
        tenant_id=uuid.UUID(int=1),
        user_id=uuid.UUID(int=2),
        org_role="member",
        principals=principals,
        auth_method="session",
    )
    return retriever.acl_filter(ctx, collection_ids=["c1"])


def test_lock_one_requires_the_collection_and_the_documents_own_list() -> None:
    flt = _filter_for(frozenset({"user:a", "dept:b"}))

    must = flt["must"]
    assert isinstance(must, list)
    assert {"key": "tenant_id", "match": {"value": str(uuid.UUID(int=1))}} in must
    assert {"key": "acl", "match": {"any": ["dept:b", "user:a"]}} in must
    # No own list (or a point indexed before lists existed), or a list naming the caller.
    assert {
        "should": [
            {"must_not": [{"key": "own_acl", "match": {"value": True}}]},
            {"key": "doc_acl", "match": {"any": ["dept:b", "user:a"]}},
        ]
    } in must
    # An agent's scope only narrows: it is one more condition every point must meet.
    assert {"key": "collection_id", "match": {"any": ["c1"]}} in must
    assert "should" not in flt  # nothing at the top level can widen it


def test_no_principals_match_nothing() -> None:
    must = _filter_for(frozenset())["must"]
    assert isinstance(must, list)
    assert {"key": "acl", "match": {"any": []}} in must
