"""Meetings without a database: transcript formats and the checks on what the model extracts."""

from datetime import UTC, date, datetime
from typing import Any

import pytest

from tests.fakes import FakeLLM
from workmind.api.v1.meetings import UnsatisfiableRangeError, byte_range
from workmind.modules.meetings import transcripts
from workmind.modules.meetings.extract import ActionItem, Decision, Extraction, clock, extract
from workmind.modules.meetings.models import Meeting, held_on
from workmind.modules.meetings.pipeline import document_title, transcript_markdown
from workmind.providers.transcription import hint

VTT = b"""WEBVTT

1
00:00:01.000 --> 00:00:04.500
<v Olivia Grant>We move the safety drill to Tuesday.</v>

2
00:00:05.000 --> 00:00:09.250
<v Wei Zhang>I'll add the <i>battery</i> alerts by Friday next week.

3
00:06:10.000 --> 00:06:12.000
<v Olivia Grant>Thanks, everyone.
"""

SRT = b"""1
00:00:01,000 --> 00:00:03,000
Samuel: I'll draft the customer notice.

2
01:02:03,500 --> 01:02:05,000
Back to the agenda.
"""


def test_vtt_keeps_times_speakers_and_strips_markup() -> None:
    t = transcripts.parse(VTT, "sync.vtt")

    assert [(s.start, s.end, s.speaker) for s in t.segments] == [
        (1.0, 4.5, "Olivia Grant"),
        (5.0, 9.25, "Wei Zhang"),
        (370.0, 372.0, "Olivia Grant"),
    ]
    assert t.segments[1].text == "I'll add the battery alerts by Friday next week."
    assert t.duration == 372.0


def test_srt_reads_hours_and_name_prefixes() -> None:
    t = transcripts.parse(SRT, "sync.srt")

    assert [(s.start, s.speaker, s.text) for s in t.segments] == [
        (1.0, "Samuel", "I'll draft the customer notice."),
        (3723.5, None, "Back to the agenda."),
    ]


def test_plain_text_keeps_the_order_with_approximate_times() -> None:
    t = transcripts.parse(b"Olivia Grant: Let's start.\n\nWei Zhang: " + b"word " * 25, "notes.txt")

    assert [s.speaker for s in t.segments] == ["Olivia Grant", "Wei Zhang"]
    assert t.segments[0].start == 0.0
    assert t.segments[1].start == t.segments[0].end
    assert t.segments[1].end - t.segments[1].start == pytest.approx(10.0)  # 25 words at 150/min


def test_file_kinds() -> None:
    assert transcripts.kind("standup.M4A") == "audio"
    assert transcripts.kind("standup.vtt") == "transcript"
    with pytest.raises(ValueError, match="unsupported"):
        transcripts.kind("standup.exe")


def _segments() -> list[dict[str, Any]]:
    t = transcripts.parse(VTT, "sync.vtt")
    return [
        {"id": f"T{i}", "start": s.start, "end": s.end, "speaker": s.speaker, "text": s.text}
        for i, s in enumerate(t.segments, start=1)
    ]


async def test_extraction_checks_segments_owners_and_dates() -> None:
    llm = FakeLLM(
        structured=[
            Extraction(
                summary=" The drill moves to Tuesday. ",
                decisions=[
                    Decision(text="Move the safety drill to Tuesday", segment="T1"),
                    Decision(text="Something never said", segment="T42"),  # no such segment
                    Decision(text="  ", segment="T1"),  # empty: dropped
                ],
                action_items=[
                    ActionItem(
                        text="Add battery alerts", owner="Wei", due="2026-11-13", segment="T2"
                    ),
                    ActionItem(text="Send the notes", owner="", due="next week", segment="[T3]"),
                ],
            )
        ]
    )

    outcome, usage = await extract(
        llm,
        _segments(),
        organization="Acme Robotics",
        day=date(2026, 11, 2),
        speakers={},
        people={"wei zhang": "u-wei", "wei": "u-wei"},
    )

    assert outcome["summary"] == "The drill moves to Tuesday."
    assert [(d["text"], d["segment"], d["start"]) for d in outcome["decisions"]] == [
        ("Move the safety drill to Tuesday", "T1", 1.0),
        ("Something never said", None, None),  # kept, but not given a made-up timestamp
    ]
    wei, notes = outcome["action_items"]
    assert (wei["owner_user_id"], wei["due"], wei["start"]) == ("u-wei", "2026-11-13", 5.0)
    assert (notes["owner_user_id"], notes["due"], notes["segment"]) == (None, None, None)
    assert usage.model == "fake"
    role, request = llm.requests[0]
    assert role == "generator"
    assert "held on 2026-11-02" in request.system
    assert "[T2] (00:05) Wei Zhang: I'll add the battery alerts" in request.messages[0].content


async def test_mapped_speaker_names_reach_the_model() -> None:
    llm = FakeLLM(structured=[Extraction(summary="", decisions=[], action_items=[])])
    segments = [{"id": "T1", "start": 0.0, "end": 2.0, "speaker": "SPEAKER_1", "text": "Hello."}]

    await extract(
        llm,
        segments,
        organization="Acme",
        day=date(2026, 11, 2),
        speakers={"SPEAKER_1": {"name": "Olivia Grant", "user_id": None}},
        people={},
    )

    assert "Olivia Grant: Hello." in llm.requests[0][1].messages[0].content


def test_the_indexed_transcript_is_split_into_five_minute_sections() -> None:
    meeting = Meeting(title="Weekly sync", segments=_segments(), speakers={})

    markdown = transcript_markdown(meeting)

    assert markdown.startswith("# Meeting: Weekly sync\n")
    assert "## 00:00 to 05:00" in markdown
    assert "## 05:00 to 10:00" in markdown
    assert "[06:10] Olivia Grant: Thanks, everyone." in markdown
    assert clock(3723.5) == "1:02:03"


def test_the_transcript_heading_is_the_document_title() -> None:
    # Indexing reads the first heading as the title; it must not rename the document.
    meeting = Meeting(
        title="Weekly sync", segments=[], speakers={}, created_at=datetime(2026, 11, 2, tzinfo=UTC)
    )

    assert document_title(meeting) == "Meeting: Weekly sync (2026-11-02)"
    assert transcript_markdown(meeting).startswith("# Meeting: Weekly sync (2026-11-02)\n")


def test_the_spelling_hint_dedupes_and_fits_the_prompt_budget() -> None:
    assert hint([]) is None
    assert hint(["Olivia Grant", " Wei Zhang ", "Olivia Grant", ""]) == "Olivia Grant, Wei Zhang"
    many = hint([f"Person Number {i}" for i in range(200)], max_chars=100)
    assert many is not None
    assert len(many) <= 100
    assert many.startswith("Person Number 0, ")


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("", None),
        ("bytes=0-", (0, 99)),
        ("bytes=10-19", (10, 19)),
        ("bytes=90-500", (90, 99)),  # past the end: up to the last byte
        ("bytes=-30", (70, 99)),  # the last 30 bytes
        ("bytes=-500", (0, 99)),
        ("bytes=0-0", (0, 0)),
        ("bytes=20-10", None),  # invalid: ignored, the whole file
        ("bytes=0-1, 5-6", None),  # several ranges: the whole file
        ("items=0-5", None),
        ("bytes=-", None),
    ],
)
def test_media_answers_one_byte_range(header: str | None, expected: tuple[int, int] | None) -> None:
    assert byte_range(header, 100) == expected


@pytest.mark.parametrize("header", ["bytes=100-", "bytes=500-600", "bytes=-0"])
def test_a_range_past_the_end_is_not_satisfiable(header: str) -> None:
    with pytest.raises(UnsatisfiableRangeError):
        byte_range(header, 100)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("weekly-ops-sync-2026-11-02.txt", date(2026, 11, 2)),
        ("2026-11-04 customer call.m4a", date(2026, 11, 4)),
        ("sync-2026-13-40-then-2026-10-30.vtt", date(2026, 10, 30)),  # not a date, then one
        ("ops-sync.vtt", date(2026, 11, 12)),  # no date: the upload's day
        ("build-20261102.vtt", date(2026, 11, 12)),
    ],
)
def test_a_meeting_is_held_on_the_date_in_its_file_name(filename: str, expected: date) -> None:
    meeting = Meeting(filename=filename, created_at=datetime(2026, 11, 12, 9, 30, tzinfo=UTC))

    assert held_on(meeting) == expected
