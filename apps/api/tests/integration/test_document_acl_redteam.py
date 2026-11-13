"""The red-team attacks on documents with their own access list, as CI runs the red-team suite
with --retrieval-only, but against the real seeded corpus loaded here (hash embeddings, real
PostgreSQL, Qdrant and OpenFGA): no attack's search, in any retrieval mode, returns the
forbidden document or its canary. The people on the lists do find them (the control), and
re-seeding keeps the lists.

Needs TEST_QDRANT_URL, TEST_VALKEY_URL and TEST_OPENFGA_URL (+ TEST_OPENFGA_API_KEY).
"""

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import httpx
import pytest
from sqlalchemy import select

from needwise.core.resources import Resources
from needwise.core.tenancy import tenant_transaction
from needwise.evals.datasets import load_red_team
from needwise.evals.runner import Askers, Evaluator
from needwise.modules.authz.openfga import OpenFgaClient
from needwise.modules.authz.sync import ensure_store, reconcile
from needwise.modules.knowledge.models import Document
from needwise.modules.knowledge.pipeline import IngestionDeps
from needwise.modules.knowledge.storage import LocalObjectStorage
from needwise.modules.retrieval.qdrant import QdrantClient
from needwise.modules.retrieval.service import Retriever
from needwise.providers.embeddings import HashEmbeddings
from needwise.seed.corpus_load import load_corpus
from tests.integration.conftest import VALKEY_URL, SessionFactory, seed_acme

QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
OPENFGA_URL = os.environ.get("TEST_OPENFGA_URL")
OPENFGA_KEY = os.environ.get("TEST_OPENFGA_API_KEY")
pytestmark = pytest.mark.skipif(
    not (QDRANT_URL and VALKEY_URL and OPENFGA_URL),
    reason="set TEST_QDRANT_URL, TEST_VALKEY_URL and TEST_OPENFGA_URL",
)
REPO = Path(__file__).resolve().parents[4]
ATTACKS = [i for i in load_red_team(REPO / "evals" / "datasets") if i.kind == "document-acl"]


@dataclass
class Seeded:
    """What the evaluator reads from Resources: the database and the retriever."""

    db: SessionFactory
    retriever: Retriever


@pytest.fixture
async def evaluator(
    owner_sessions: SessionFactory, app_sessions: SessionFactory, tmp_path: Path
) -> AsyncIterator[tuple[Evaluator, IngestionDeps, uuid.UUID]]:
    acme = await seed_acme(owner_sessions)
    name = f"chunks_test_{uuid.uuid4().hex[:8]}_64"
    async with httpx.AsyncClient(timeout=60) as http:
        qdrant = QdrantClient(http, QDRANT_URL or "")
        fga = OpenFgaClient(http, OPENFGA_URL or "", OPENFGA_KEY)
        embeddings = HashEmbeddings(64)
        deps = IngestionDeps(
            app_sessions, LocalObjectStorage(str(tmp_path)), embeddings, qdrant, name
        )
        loaded = await load_corpus(
            deps,
            acme.tenant_id,
            REPO / "seed" / "acme" / "corpus",
            uploader_email="priya.raman@acme.test",
        )
        assert loaded.failed == []
        store = await ensure_store(owner_sessions, fga, acme.tenant_id)
        await reconcile(owner_sessions, fga, acme.tenant_id, store)  # lists included
        retriever = Retriever(
            qdrant=qdrant,
            embeddings=embeddings,
            fga=fga,
            sessions=app_sessions,
            collection_name=name,
        )
        resources = cast(Resources, Seeded(app_sessions, retriever))
        evaluator = Evaluator(resources, Askers(resources, acme.slug))
        yield evaluator, deps, acme.tenant_id
        await qdrant.delete_collection(name)
        await fga.delete_store(store)


async def test_no_document_acl_attack_finds_its_target_in_any_mode(
    evaluator: tuple[Evaluator, IngestionDeps, uuid.UUID],
) -> None:
    runner, _, _ = evaluator
    assert len(ATTACKS) >= 8

    records = await runner.context_checks(ATTACKS, ["hybrid", "dense", "sparse"])

    assert len(records) == len(ATTACKS) * 3
    assert [(r.id, r.mode, r.leaks) for r in records if r.leaks] == []


async def test_the_people_on_the_lists_find_them(
    evaluator: tuple[Evaluator, IngestionDeps, uuid.UUID],
) -> None:
    runner, _, _ = evaluator

    async def found(person: str, question: str, canary: str) -> bool:
        ctx = await runner.askers.context(person)
        result = await runner.resources.retriever.search(ctx, question, limit=20, mode="sparse")
        return any(canary in p.text for p in result.passages)

    # The paragraphs that carry the code words (keyword search: hash embeddings are crude).
    advisory = "embargoed until the fix ships: do not forward, do not discuss in public channels"
    reorg = "draft for the leadership team and the HR managers, not announced, leadership offsite"
    assert await found("wei.zhang", advisory, "CANARY-DOCACL-7Z4")
    assert await found("marcus.bell", advisory, "CANARY-DOCACL-7Z4")
    assert await found("alice", reorg, "CANARY-REORG-3V8")  # an HR manager
    assert await found("priya.raman", reorg, "CANARY-REORG-3V8")  # leadership
    assert not await found("tom.becker", reorg, "CANARY-REORG-3V8")  # HR, not a manager
    assert not await found("bob", advisory, "CANARY-DOCACL-7Z4")


async def test_reseeding_keeps_the_lists(
    evaluator: tuple[Evaluator, IngestionDeps, uuid.UUID],
) -> None:
    runner, deps, tenant_id = evaluator

    again = await load_corpus(
        deps, tenant_id, REPO / "seed" / "acme" / "corpus", uploader_email="priya.raman@acme.test"
    )

    assert (again.indexed, again.failed) == (0, [])
    async with tenant_transaction(deps.sessions, tenant_id) as session:
        listed = set(
            await session.scalars(select(Document.filename).where(Document.own_acl.is_(True)))
        )
    assert listed == {
        "security-advisory-nw-sa-2026-07-embargoed.md",
        "organisation-changes-2027-draft.md",
    }
    records = await runner.context_checks(ATTACKS, ["hybrid"])
    assert [r.leaks for r in records if r.leaks] == []
