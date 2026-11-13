"""Speaker labels without a database: diarization's turns on the transcript, naming speakers, and
the diarization models' download checks."""

import hashlib
import io
import tarfile
from pathlib import Path
from typing import Any

import pytest

from workmind.modules.meetings import pipeline
from workmind.modules.meetings.speakers import (
    assign_speakers,
    relabel,
    relink_owners,
    speaker_at,
)
from workmind.providers import diarization
from workmind.providers.diarization import (
    DiarizationError,
    DisabledDiarizer,
    ModelFile,
    Turn,
    fetch,
)
from workmind.providers.transcription import Segment

TURNS = [Turn(0.0, 4.0, 3), Turn(4.5, 9.0, 0), Turn(9.2, 12.0, 3)]


def test_a_segment_takes_the_speaker_heard_most_while_it_was_said() -> None:
    assert speaker_at(TURNS, 0.5, 3.5) == 3
    assert speaker_at(TURNS, 3.0, 8.0) == 0  # 1 s of speaker 3, 3.5 s of speaker 0
    assert speaker_at(TURNS, 4.0, 4.5) == 0  # in the gap, as near to both: the lower number


def test_speech_in_a_gap_goes_to_the_nearest_turn_close_enough() -> None:
    turns = [Turn(0.0, 2.0, 1), Turn(10.0, 12.0, 2)]

    assert speaker_at(turns, 2.3, 2.6) == 1  # 0.45 s after speaker 1 stopped
    assert speaker_at(turns, 9.2, 9.6) == 2
    assert speaker_at(turns, 5.0, 6.0) is None  # nobody within a second
    assert speaker_at([], 0.0, 1.0) is None


def test_ties_go_to_the_lower_speaker_number() -> None:
    assert speaker_at([Turn(0.0, 1.0, 5), Turn(1.0, 2.0, 2)], 0.5, 1.5) == 2


def test_labels_are_numbered_in_order_of_first_appearance() -> None:
    segments = [
        Segment(0.2, 3.8, "Morning, everyone."),
        Segment(4.6, 8.0, "Morning. All here."),
        Segment(9.3, 11.5, "Let's start."),
        Segment(20.0, 21.0, "(nobody near)"),
    ]

    labelled = assign_speakers(segments, TURNS)

    assert [s.speaker for s in labelled] == ["SPEAKER_1", "SPEAKER_2", "SPEAKER_1", None]
    assert [s.text for s in labelled] == [s.text for s in segments]
    assert [(s.start, s.end) for s in labelled] == [(s.start, s.end) for s in segments]


def test_without_turns_the_transcript_is_unchanged() -> None:
    segments = [Segment(0.0, 1.0, "Hello.")]

    assert assign_speakers(segments, []) == segments


SPEAKERS: dict[str, Any] = {
    "SPEAKER_1": {"name": "Olivia Grant", "user_id": "u-olivia"},
    "SPEAKER_2": {"name": "SPEAKER_2", "user_id": None},
    "SPEAKER_3": {"name": "Jonas Brandt", "user_id": None, "guest": True},
}


def test_labels_the_model_wrote_show_the_names_they_are_mapped_to() -> None:
    text = "SPEAKER_1 asked SPEAKER_3 and SPEAKER_2; SPEAKER_12 and XSPEAKER_1 are not labels."

    assert relabel(text, SPEAKERS) == (
        "Olivia Grant asked Jonas Brandt and SPEAKER_2; SPEAKER_12 and XSPEAKER_1 are not labels."
    )
    assert relabel("Wei will do it", SPEAKERS) == "Wei will do it"  # names are never rewritten


def test_naming_a_speaker_relinks_the_items_they_own_but_not_tasks() -> None:
    items = [
        {"text": "Send the quote", "owner": "SPEAKER_2", "owner_user_id": None},
        {"text": "Book the room", "owner": "speaker_2", "owner_user_id": None},
        {"text": "Already a task", "owner": "SPEAKER_2", "owner_user_id": None, "task_id": "t1"},
        {"text": "Someone else's", "owner": "SPEAKER_1", "owner_user_id": "u-olivia"},
        {"text": "Nobody's", "owner": "", "owner_user_id": None},
    ]

    out, relinked = relink_owners(
        items, {"SPEAKER_2": {"name": "Wei Zhang", "user_id": "u-wei"}}, {"SPEAKER_2": "SPEAKER_2"}
    )

    assert relinked == 2
    assert [(i["owner"], i["owner_user_id"]) for i in out] == [
        ("Wei Zhang", "u-wei"),
        ("Wei Zhang", "u-wei"),
        ("SPEAKER_2", None),  # a task: change the task instead
        ("SPEAKER_1", "u-olivia"),
        ("", None),
    ]
    assert items[0]["owner"] == "SPEAKER_2"  # the input is not changed


def test_renaming_again_follows_the_previous_name_and_guests_link_nobody() -> None:
    items = [{"text": "Send the quote", "owner": "Wei Zhang", "owner_user_id": "u-wei"}]

    out, _ = relink_owners(
        items,
        {"SPEAKER_2": {"name": "Jonas Brandt", "user_id": None, "guest": True}},
        {"SPEAKER_2": "Wei Zhang"},
    )

    assert (out[0]["owner"], out[0]["owner_user_id"]) == ("Jonas Brandt", None)


# --- The diarization step in the pipeline ---------------------------------------------------


class _Fixed:
    model_id = "fixed"

    def __init__(self, turns: list[Turn]) -> None:
        self.turns = turns

    async def diarize(self, data: bytes, filename: str) -> list[Turn]:
        return self.turns


async def test_the_pipeline_records_who_diarized_and_how_many_speakers() -> None:
    timings: dict[str, Any] = {}

    turns = await pipeline._diarize(_Fixed(TURNS), b"audio", "sync.m4a", timings)  # pyright: ignore[reportPrivateUsage]

    assert turns == TURNS
    assert (timings["diarizer"], timings["speakers_found"]) == ("fixed", 2)
    assert "diarize_s" in timings


async def test_without_diarization_a_recording_is_transcribed_unlabelled_with_the_reason() -> None:
    timings: dict[str, Any] = {}

    turns = await pipeline._diarize(DisabledDiarizer(), b"audio", "sync.m4a", timings)  # pyright: ignore[reportPrivateUsage]

    assert turns == []
    assert timings == {
        "diarization_skipped": "speaker diarization is off (DIARIZATION_PROVIDER=none)"
    }


# --- Model downloads --------------------------------------------------------------------------


def _archive(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_a_model_is_downloaded_once_and_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    onnx = b"onnx model bytes"
    archive = _archive({"pack/model.onnx": onnx, "pack/README.md": b"hi"})
    downloads: list[str] = []

    def download(url: str) -> bytes:
        downloads.append(url)
        return archive

    monkeypatch.setattr(diarization, "_download", download)
    model = ModelFile(
        "seg.onnx", "https://x/pack.tar.bz2", _sha(archive), "pack/model.onnx", _sha(onnx)
    )

    path = fetch(model, tmp_path / "models")
    again = fetch(model, tmp_path / "models")

    assert path == again == tmp_path / "models" / "seg.onnx"
    assert path.read_bytes() == onnx
    assert downloads == ["https://x/pack.tar.bz2"]  # the second call found it
    assert sorted(p.name for p in path.parent.iterdir()) == ["seg.onnx"]  # no leftovers


def test_a_model_that_does_not_match_its_digest_is_not_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diarization, "_download", lambda url: b"tampered")
    model = ModelFile("emb.onnx", "https://x/emb.onnx", _sha(b"the real model"))

    with pytest.raises(DiarizationError, match="pinned SHA-256"):
        fetch(model, tmp_path)
    assert not (tmp_path / "emb.onnx").exists()


def test_an_archive_must_hold_the_expected_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _archive({"pack/other.onnx": b"x"})
    monkeypatch.setattr(diarization, "_download", lambda url: archive)
    model = ModelFile("seg.onnx", "https://x/a.tar.bz2", _sha(archive), "pack/model.onnx", "0")

    with pytest.raises(DiarizationError, match="not in the model archive"):
        fetch(model, tmp_path)


def test_the_default_models_are_pinned() -> None:
    for model in (diarization.SEGMENTATION, diarization.EMBEDDING):
        assert model.url.startswith("https://github.com/k2-fsa/sherpa-onnx/releases/download/")
        assert len(model.sha256) == 64
    assert diarization.SEGMENTATION.member_sha256 is not None


async def test_the_disabled_diarizer_says_why() -> None:
    with pytest.raises(DiarizationError, match="DIARIZATION_PROVIDER=none"):
        await DisabledDiarizer().diarize(b"", "x.m4a")
