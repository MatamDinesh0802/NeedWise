"""Who spoke when (speaker diarization), behind one interface like speech to text:

- SherpaOnnxDiarizer: pyannote's segmentation-3.0 model finds speech and speaker changes, a
  speaker-embedding model (NVIDIA NeMo TitaNet-S) tells voices apart, and the embeddings are
  clustered into speakers. Both models are ONNX files run by sherpa-onnx on the CPU (the
  `diarization` extra): no PyTorch, no Hugging Face account. They download on first use from
  sherpa-onnx's GitHub releases and are checked against the SHA-256 digests pinned below.
- DisabledDiarizer: DIARIZATION_PROVIDER=none.

pyannote.audio itself is not used: it needs PyTorch and its pretrained pipelines are gated behind
a Hugging Face token. Anything with a `diarize(data, filename) -> list[Turn]` method can take
this one's place (docs/meetings.md).

The libraries are optional and untyped, so they are imported as modules of type Any.
"""

import asyncio
import gc
import hashlib
import importlib
import io
import itertools
import os
import tarfile
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

EXTRA = "install the diarization extra: uv sync --extra diarization"
_RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
_MAX_DOWNLOAD = 200 * 1024 * 1024


class DiarizationError(Exception):
    pass


@dataclass(frozen=True)
class Turn:
    """One stretch of one speaker's voice. Speakers are numbered by the diarizer (0, 1, ...)."""

    start: float  # seconds
    end: float
    speaker: int


class Diarizer(Protocol):
    model_id: str

    async def diarize(self, data: bytes, filename: str) -> list[Turn]: ...


@dataclass(frozen=True)
class ModelFile:
    """A model to download once: `url` must hash to `sha256`. For an archive, `member` is the
    file to keep from it, and must hash to `member_sha256`."""

    name: str  # the file name in the model directory
    url: str
    sha256: str
    member: str | None = None
    member_sha256: str | None = None


# pyannote/segmentation-3.0 (MIT, CNRS), as exported to ONNX by sherpa-onnx.
SEGMENTATION = ModelFile(
    name="pyannote-segmentation-3-0.onnx",
    url=f"{_RELEASES}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
    sha256="24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488",
    member="sherpa-onnx-pyannote-segmentation-3-0/model.onnx",
    member_sha256="220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079",
)
# NVIDIA NeMo TitaNet-S speaker embeddings (the NeMo toolkit's license, Apache-2.0).
EMBEDDING = ModelFile(
    name="nemo_en_titanet_small.onnx",
    url=f"{_RELEASES}/speaker-recongition-models/nemo_en_titanet_small.onnx",
    sha256="ad4a1802485d8b34c722d2a9d04249662f2ece5d28a7a039063ca22f515a789e",
)


def _module(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise DiarizationError(EXTRA) from exc


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_atomically(path: Path, data: bytes) -> None:
    """Concurrent workers may both download: each writes its own file, the rename is atomic."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _download(url: str) -> bytes:
    try:
        with (
            httpx.Client(follow_redirects=True, timeout=300) as http,
            http.stream("GET", url) as response,
        ):
            if response.is_error:
                raise DiarizationError(f"model download failed: HTTP {response.status_code}")
            body = bytearray()
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > _MAX_DOWNLOAD:
                    raise DiarizationError("model download too large")
            return bytes(body)
    except httpx.HTTPError as exc:
        raise DiarizationError(f"model download failed: {exc}") from exc


def _from_archive(archive: bytes, member: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:bz2") as tar:
        try:
            info = tar.getmember(member)
        except KeyError as exc:
            raise DiarizationError(f"{member} is not in the model archive") from exc
        extracted = tar.extractfile(info) if info.isfile() else None
        if extracted is None:
            raise DiarizationError(f"{member} is not a file")
        return extracted.read()


def fetch(model: ModelFile, directory: Path) -> Path:
    """The model's path in `directory`, downloaded and verified on first use."""
    path = directory / model.name
    if path.is_file():
        return path
    data = _download(model.url)
    if _sha256(data) != model.sha256:
        raise DiarizationError(f"{model.url} does not match its pinned SHA-256")
    if model.member:
        data = _from_archive(data, model.member)
        if _sha256(data) != model.member_sha256:
            raise DiarizationError(f"{model.member} does not match its pinned SHA-256")
    _write_atomically(path, data)
    return path


def _valid_frames(frames: Iterator[Any], av: Any) -> Iterator[Any]:
    while True:
        try:
            yield next(frames)
        except StopIteration:
            return
        except av.error.InvalidDataError:
            continue


def decode_audio(data: bytes, rate: int) -> Any:
    """Mono float32 samples at `rate` Hz (a numpy array), decoded by PyAV like faster-whisper
    does, so both read the same audio."""
    av, np = _module("av"), _module("numpy")
    resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
    pcm = io.BytesIO()
    try:
        with av.open(io.BytesIO(data), mode="r") as container:
            if not container.streams.audio:
                raise DiarizationError("the file has no audio")
            frames = _valid_frames(iter(container.decode(audio=0)), av)
            for frame in itertools.chain(frames, [None]):
                for out in resampler.resample(frame):
                    pcm.write(out.to_ndarray().tobytes())
    except av.error.FFmpegError as exc:
        raise DiarizationError(f"could not decode the audio: {exc}") from exc
    finally:
        del resampler
        gc.collect()  # PyAV's resampler holds native memory until collected
    return np.frombuffer(pcm.getbuffer(), dtype=np.int16).astype(np.float32) / 32768.0


class SherpaOnnxDiarizer:
    """`threshold`: how far apart two voices must be to count as two speakers (higher: fewer
    speakers). On short recordings a high one merges different voices, which naming speakers
    cannot undo, while a low one at worst splits a voice into two labels, which naming both
    fixes. `window_shift`: the step of pyannote's 10-second analysis window, as a share of it
    (pyannote's own is 0.1: about four times slower)."""

    def __init__(
        self,
        model_dir: Path,
        *,
        threshold: float = 0.6,
        window_shift: float = 0.5,
        threads: int = 1,  # more did not make it faster on a 10-core Apple M5
    ) -> None:
        self.model_id = "sherpa-onnx/pyannote-segmentation-3.0+nemo-titanet-small"
        self._dir = model_dir
        self._threshold, self._shift, self._threads = threshold, window_shift, threads
        self._engine: Any = None
        self._lock = threading.Lock()  # one recording at a time per process: memory-bound

    def _load(self) -> Any:
        if self._engine is None:
            sherpa = _module("sherpa_onnx")
            # Placed there by hand (an offline install), or downloaded once.
            segmentation, embedding = fetch(SEGMENTATION, self._dir), fetch(EMBEDDING, self._dir)
            config = sherpa.OfflineSpeakerDiarizationConfig(
                segmentation=sherpa.OfflineSpeakerSegmentationModelConfig(
                    pyannote=sherpa.OfflineSpeakerSegmentationPyannoteModelConfig(
                        model=str(segmentation), window_shift_ratio=self._shift
                    ),
                    num_threads=self._threads,
                ),
                embedding=sherpa.SpeakerEmbeddingExtractorConfig(
                    model=str(embedding), num_threads=self._threads
                ),
                clustering=sherpa.FastClusteringConfig(num_clusters=-1, threshold=self._threshold),
                min_duration_on=0.3,
                min_duration_off=0.5,
            )
            if not config.validate():
                raise DiarizationError("the diarization models could not be loaded")
            self._engine = sherpa.OfflineSpeakerDiarization(config)
        return self._engine

    def _run(self, data: bytes) -> list[Turn]:
        with self._lock:
            engine = self._load()
            samples = decode_audio(data, int(engine.sample_rate))
            if len(samples) == 0:
                return []
            result = engine.process(samples).sort_by_start_time()
            return [Turn(float(r.start), float(r.end), int(r.speaker)) for r in result]

    async def diarize(self, data: bytes, filename: str) -> list[Turn]:
        try:
            return await asyncio.to_thread(self._run, data)
        except DiarizationError:
            raise
        except Exception as exc:  # a corrupt file, or a model that will not load
            raise DiarizationError(
                f"could not tell the speakers in {filename} apart: {exc}"
            ) from exc


class DisabledDiarizer:
    model_id = "none"

    async def diarize(self, data: bytes, filename: str) -> list[Turn]:
        raise DiarizationError("speaker diarization is off (DIARIZATION_PROVIDER=none)")
