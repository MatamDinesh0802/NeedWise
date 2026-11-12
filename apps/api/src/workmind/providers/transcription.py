# faster-whisper is optional (the `meetings` extra) and ships without type information.
# pyright: reportMissingTypeStubs=false, reportMissingImports=false
"""Speech to text, behind one interface like the other model adapters (ADR 0007):

- FasterWhisperTranscriber: local, in the worker (CPU; `pip install workmind[meetings]`).
- OpenAICompatibleTranscriber: any `/v1/audio/transcriptions` server (speaches, whisper.cpp's
  server, hosted APIs), asking for `verbose_json` segments with timestamps.
"""

import asyncio
import io
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

import httpx


class TranscriptionError(Exception):
    pass


@dataclass(frozen=True)
class Segment:
    start: float  # seconds
    end: float
    text: str
    speaker: str | None = None


@dataclass
class Transcript:
    segments: list[Segment] = field(default_factory=list[Segment])
    language: str | None = None
    duration: float | None = None


class Transcriber(Protocol):
    model_id: str

    async def transcribe(
        self, data: bytes, filename: str, *, vocabulary: Sequence[str] = ()
    ) -> Transcript:
        """vocabulary: words to expect (colleagues' names), so they are spelled right."""
        ...


def hint(vocabulary: Sequence[str], *, max_chars: int = 600) -> str | None:
    """A comma-separated hint within Whisper's prompt budget (about 220 tokens)."""
    words: list[str] = []
    for word in dict.fromkeys(w.strip() for w in vocabulary if w.strip()):
        if sum(len(w) + 2 for w in words) + len(word) > max_chars:
            break
        words.append(word)
    return ", ".join(words) or None


class FasterWhisperTranscriber:
    def __init__(self, model: str = "base.en", *, compute_type: str = "int8") -> None:
        self.model_id = f"faster-whisper/{model}"
        self._name, self._compute = model, compute_type
        self._model: Any = None
        self._lock = threading.Lock()  # one decode at a time per process: memory-bound

    def _load(self) -> Any:  # pyright: ignore[reportUnknownParameterType]
        if self._model is None:
            try:
                # Optional (the `meetings` extra) and untyped.
                from faster_whisper import (
                    WhisperModel,  # pyright: ignore[reportMissingTypeStubs, reportMissingImports, reportUnknownVariableType]
                )
            except ImportError as exc:
                raise TranscriptionError(
                    "install the meetings extra: uv sync --extra meetings"
                ) from exc
            # cast: without the extra installed, WhisperModel is Unknown to pyright.
            self._model = cast(
                Any, WhisperModel(self._name, device="cpu", compute_type=self._compute)
            )
        return self._model

    def _run(self, data: bytes, hotwords: str | None) -> Transcript:
        with self._lock:
            model = self._load()
            # hotwords prime every 30-second window (initial_prompt only the first).
            segments, info = model.transcribe(
                io.BytesIO(data), vad_filter=True, beam_size=1, hotwords=hotwords
            )
            out = [Segment(float(s.start), float(s.end), str(s.text).strip()) for s in segments]
        return Transcript(out, getattr(info, "language", None), getattr(info, "duration", None))

    async def transcribe(
        self, data: bytes, filename: str, *, vocabulary: Sequence[str] = ()
    ) -> Transcript:
        try:
            return await asyncio.to_thread(self._run, data, hint(vocabulary))
        except TranscriptionError:
            raise
        except Exception as exc:  # a corrupt or unsupported file
            raise TranscriptionError(f"could not transcribe {filename}: {exc}") from exc


class OpenAICompatibleTranscriber:
    def __init__(
        self, http: httpx.AsyncClient, *, base_url: str, model: str, api_key: str | None = None
    ) -> None:
        self.model_id = model
        self._http = http
        self._url = base_url.rstrip("/") + "/audio/transcriptions"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def transcribe(
        self, data: bytes, filename: str, *, vocabulary: Sequence[str] = ()
    ) -> Transcript:
        form = {"model": self.model_id, "response_format": "verbose_json"}
        if prompt := hint(vocabulary):
            form["prompt"] = prompt
        try:
            response = await self._http.post(
                self._url,
                headers=self._headers,
                data=form,
                files={"file": (filename, data)},
                timeout=1800,
            )
        except httpx.HTTPError as exc:
            raise TranscriptionError(f"transcription server unreachable: {exc}") from exc
        if response.is_error:
            raise TranscriptionError(f"transcription server HTTP {response.status_code}")
        body = response.json()
        raw: Sequence[dict[str, Any]] = body.get("segments") or []
        segments = [
            Segment(float(s["start"]), float(s["end"]), str(s["text"]).strip()) for s in raw
        ]
        if not segments and body.get("text"):
            segments = [Segment(0.0, float(body.get("duration") or 0), str(body["text"]).strip())]
        return Transcript(segments, body.get("language"), body.get("duration"))


class DisabledTranscriber:
    model_id = "none"

    async def transcribe(
        self, data: bytes, filename: str, *, vocabulary: Sequence[str] = ()
    ) -> Transcript:
        raise TranscriptionError(
            "audio transcription is off: set TRANSCRIPTION_PROVIDER, or upload a transcript"
        )
