"""Parsing, chunking, keyword encoding, sensitivity and ACL derivation, without any services."""

import json
import uuid

import pytest

from workmind.modules.knowledge.acl import acl_principals, collection_tuples, openfga_user
from workmind.modules.knowledge.chunking import (
    MAX_TOKENS,
    chunk_document,
    embedding_text,
    estimate_tokens,
)
from workmind.modules.knowledge.models import Collection, CollectionGrant
from workmind.modules.knowledge.parsing import UnsupportedFormatError, detect_mime, parse
from workmind.modules.knowledge.sensitivity import scan, suggest
from workmind.modules.retrieval.sparse import encode_document, encode_query, tokenize
from workmind.providers.embeddings import HashEmbeddings
from workmind.seed.corpus import missing_values, to_docx, to_pdf, to_pptx, to_xlsx

MARKDOWN = """# Leave Policy 2026

## 1. Annual leave
Every employee receives 28 days of paid annual leave.

## 2. Parental leave
### 2.1 Duration
Parental leave is 16 weeks, fully paid.

| Level | Minimum | Maximum |
|---|---|---|
| L3 | 68,000 | 82,000 |
"""


def _parse(data: bytes, filename: str):
    return parse(data, filename, detect_mime(filename))


def test_markdown_keeps_title_and_heading_paths() -> None:
    document = _parse(MARKDOWN.encode(), "leave.md")

    assert document.title == "Leave Policy 2026"
    assert [s.heading_path for s in document.sections] == [
        ("1. Annual leave",),
        ("2. Parental leave", "2.1 Duration"),
    ]


@pytest.mark.parametrize(("render", "filename"), [(to_docx, "leave.docx"), (to_pdf, "leave.pdf")])
def test_office_and_pdf_round_trips_keep_the_facts(render, filename) -> None:
    document = _parse(render(MARKDOWN, "Leave Policy 2026"), filename)
    text = "\n".join(s.text for s in document.sections)

    assert document.title == "Leave Policy 2026"
    assert "16 weeks" in text
    assert "68,000" in text or "68000" in text


def test_spreadsheets_become_tables_that_repeat_their_header() -> None:
    rows = "\n".join(f"| L{i} | {i}0,000 | {i}5,000 |" for i in range(1, 60))
    markdown = f"# Bands\n\n## Engineering\n\n| Level | Min | Max |\n|---|---|---|\n{rows}\n"
    document = _parse(to_xlsx(markdown, "Bands"), "bands.xlsx")

    chunks = chunk_document(document)
    assert len(chunks) > 1
    assert all(c.text.startswith("| Level | Min | Max |") for c in chunks)
    assert all(c.heading_path == ("Engineering",) for c in chunks)


def test_slides_become_one_section_each() -> None:
    markdown = (
        "# Roadmap\n\n## Q3 priority\n- Battery telemetry\n\n## Q4 priority\n- Multi-site routing\n"
    )
    document = _parse(to_pptx(markdown, "Roadmap"), "roadmap.pptx")

    assert [s.heading_path[0] for s in document.sections][-2:] == [
        "Slide 2: Q3 priority",
        "Slide 3: Q4 priority",
    ]


def test_slack_exports_group_messages_by_thread() -> None:
    export = {
        "channel": "#general",
        "messages": [
            {
                "user_name": "Dan",
                "text": "Parental leave is 20 weeks now",
                "ts": "1",
                "thread_ts": None,
            },
            {"user_name": "Tom", "text": "Not sure about that", "ts": "2", "thread_ts": "1"},
            {"user_name": "Erin", "text": "Lunch at 12?", "ts": "3", "thread_ts": None},
        ],
    }
    document = _parse(json.dumps(export).encode(), "export.json")

    assert document.title == "Slack #general"
    assert len(document.sections) == 2
    assert (
        document.sections[0].text == "Dan: Parental leave is 20 weeks now\nTom: Not sure about that"
    )


def test_unknown_types_are_rejected() -> None:
    with pytest.raises(UnsupportedFormatError):
        detect_mime("payload.exe", "application/octet-stream")


def test_chunks_respect_the_budget_and_never_cross_sections() -> None:
    long_section = "## Rules\n\n" + "\n\n".join(f"Rule {i}. " + "word " * 120 for i in range(8))
    chunks = chunk_document(_parse(f"# Handbook\n\n{long_section}".encode(), "handbook.md"))

    assert len(chunks) > 3
    assert all(c.token_count <= MAX_TOKENS for c in chunks)
    assert {c.heading_path for c in chunks} == {("Rules",)}
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_the_embedded_text_carries_its_heading_context() -> None:
    assert (
        embedding_text(("2. Parental leave", "2.1 Duration"), "16 weeks")
        == "2. Parental leave > 2.1 Duration\n16 weeks"
    )
    assert estimate_tokens("| 1 | 2 | 3 |" * 10) > estimate_tokens("one two three")


def test_keyword_encoding_finds_identifiers_and_their_parts() -> None:
    assert {"inc-2026-031", "inc", "2026", "031"} <= set(tokenize("Postmortem for INC-2026-031"))
    document, query = (
        encode_document("Postmortem for INC-2026-031: expired TLS certificate"),
        encode_query("INC-2026-031"),
    )

    assert document.indices == sorted(set(document.indices))
    assert set(query.indices) <= set(document.indices)
    assert "the" not in tokenize("the policy")


@pytest.mark.parametrize(
    ("text", "default", "expected"),
    [
        ("Meals are reimbursed up to 60 euros.", "internal", "internal"),
        ("The L3 salary band is 68,000 to 82,000 euros.", "internal", "confidential"),
        ("Pay to IBAN NL91 ABNA 0417 1643 00.", "internal", "restricted"),
        ("A plain note.", "restricted", "restricted"),  # never lowered
    ],
)
def test_sensitivity_suggestions_only_ever_raise_the_default(
    text: str, default: str, expected: str
) -> None:
    assert suggest(default, scan(text)) == expected


def test_acl_and_openfga_tuples_come_from_the_same_grants() -> None:
    tenant, department, group, owner = (uuid.uuid4() for _ in range(4))
    collection = Collection(
        id=uuid.uuid4(),
        tenant_id=tenant,
        key="hr",
        name="HR",
        scope="department",
        department_id=department,
        owner_id=owner,
    )
    grants = [
        CollectionGrant(principal=f"dept:{department}", relation="viewer"),
        CollectionGrant(principal=f"group:{group}", relation="editor"),
    ]

    assert acl_principals(collection, grants) == sorted(
        {f"dept:{department}", f"group:{group}", f"user:{owner}"}
    )
    tuples = {(t.user, t.relation) for t in collection_tuples(tenant, collection, grants)}
    assert (f"department:{department}#member", "viewer") in tuples
    assert (f"group:{group}#member", "editor") in tuples
    assert (f"user:{owner}", "owner") in tuples
    assert openfga_user(f"dept-manager:{department}") == f"department:{department}#manager"


async def test_hash_embeddings_are_deterministic_and_lexical() -> None:
    embeddings = HashEmbeddings(64)
    query = await embeddings.embed_query("parental leave weeks")
    near, far = await embeddings.embed_documents(
        ["parental leave is 16 weeks", "restart the fleet controller"]
    )

    def dot(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert query == await embeddings.embed_query("parental leave weeks")
    assert dot(query, near) > dot(query, far)


def test_corpus_fact_checker_spots_missing_values() -> None:
    facts = [
        "Parental leave is 16 weeks.",
        "Record ID CANARY-SALARY-4F9",
        "Payouts were 182,400 euros.",
    ]
    assert missing_values("16 weeks, CANARY-SALARY-4F9 and 182 400 euros", facts) == []
    assert missing_values("16 weeks only", facts) == ["182,400", "CANARY-SALARY-4F9"]
