"""Speaker diarization with the real models: the `diarization` extra and its two ONNX files, on
the start of the demo recording, where five synthetic voices take turns.

Skipped unless both models are already in the model directory (DIARIZATION_MODEL_DIR, else the
default): tests do not download 46 MB. To get them, process one recording, or run
evals/experiments/meeting_diarization.py once.
"""

import importlib.util
import io
import wave
from pathlib import Path

import pytest

from workmind.core.config import Settings
from workmind.modules.meetings.speakers import assign_speakers, speaker_at
from workmind.providers.diarization import (
    EMBEDDING,
    SEGMENTATION,
    SherpaOnnxDiarizer,
    decode_audio,
)
from workmind.providers.factory import diarization_model_dir
from workmind.providers.transcription import Segment

AUDIO = (
    Path(__file__).resolve().parents[4]
    / "seed"
    / "acme"
    / "meetings"
    / "weekly-ops-sync-2026-11-02.m4a"
)
MODELS = diarization_model_dir(Settings(_env_file=None))  # pyright: ignore[reportCallIssue]
pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("sherpa_onnx") is None
    or not (MODELS / SEGMENTATION.name).is_file()
    or not (MODELS / EMBEDDING.name).is_file(),
    reason=f"needs the diarization extra and its models in {MODELS}",
)
RATE = 16_000


def _clip(seconds: float) -> bytes:
    """The recording's first `seconds` as a 16 kHz mono WAV file."""
    np = pytest.importorskip("numpy")
    samples = decode_audio(AUDIO.read_bytes(), RATE)[: int(seconds * RATE)]
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(pcm)
    return buffer.getvalue()


async def test_the_voices_at_the_start_of_the_demo_meeting_are_told_apart() -> None:
    """The first seven lines (about 66 s): Olivia, Wei, Samuel, Dan, Olivia, Dan, Hannah. This
    checks the pipeline with its real models, not its accuracy: on a clip this short the voices
    are harder to tell apart than in the whole meeting (the experiment measures both)."""
    diarizer = SherpaOnnxDiarizer(MODELS)

    turns = await diarizer.diarize(_clip(66), "start.wav")

    assert all(t.end > t.start for t in turns)
    assert len({t.speaker for t in turns}) >= 3
    olivia = [speaker_at(turns, 1.0, 12.0), speaker_at(turns, 27.5, 30.5)]
    dan = [speaker_at(turns, 23.0, 26.0), speaker_at(turns, 32.5, 38.5)]
    assert olivia[0] is not None
    assert olivia[0] == olivia[1]  # she comes back as herself
    assert dan[0] == dan[1] != olivia[0]
    # Labelled whole: a segment said while Olivia opened the meeting is hers.
    (opening,) = assign_speakers([Segment(2.0, 8.0, "Morning, everyone.")], turns)
    assert opening.speaker == "SPEAKER_1"


async def test_a_file_without_audio_is_refused_with_a_reason() -> None:
    from workmind.providers.diarization import DiarizationError

    with pytest.raises(DiarizationError, match="could not"):
        await SherpaOnnxDiarizer(MODELS).diarize(b"not audio at all", "notes.m4a")
