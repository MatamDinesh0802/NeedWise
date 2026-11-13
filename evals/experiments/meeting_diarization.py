"""Speaker diarization on the demo recording: how long it takes, and how many words end up with
the right speaker. No model calls and no database.

    cd apps/api && uv sync --extra meetings --extra diarization
    uv run python ../../evals/experiments/meeting_diarization.py [--threshold 0.6] [--shift 0.5]

Ground truth: the recording was made line by line from the transcript (make_audio.py), so each
line's speaker is known. Whisper's words are aligned to the transcript's words (difflib); a word
counts as right when the label of the segment it ended up in is mapped to its speaker. Labels are
mapped the way a person would in the app: each to the participant most of its words belong to
("after naming each label"), and, stricter, one label per participant ("one to one", greedy by
word count). Times are wall-clock seconds on this machine's CPU, with the steps run one after
another and then both at once, twice each. Then the clustering threshold is swept over the
recording's first 2, 5 and 10 minutes and all of it: fewer people's turns to cluster changes
which threshold works.
"""

import argparse
import asyncio
import difflib
import io
import json
import os
import platform
import re
import subprocess
import time
import wave
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from workmind.core.config import Settings
from workmind.evals.datasets import load_meetings
from workmind.evals.runner import score_topics
from workmind.modules.meetings.speakers import assign_speakers
from workmind.modules.meetings.topics import word_outline
from workmind.providers.diarization import SherpaOnnxDiarizer, decode_audio
from workmind.providers.factory import diarization_model_dir
from workmind.providers.transcription import FasterWhisperTranscriber, Segment

ROOT = Path(__file__).resolve().parents[2]
MEETINGS = ROOT / "seed" / "acme" / "meetings"
AUDIO = MEETINGS / "weekly-ops-sync-2026-11-02.m4a"
TRANSCRIPT = MEETINGS / "weekly-ops-sync-2026-11-02.txt"
RATE = 16_000
CUTS = (120, 300, 600)  # seconds
THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9)


def wav(samples: Any) -> bytes:
    """Float samples as a 16 kHz mono WAV file."""
    import numpy as np

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes((np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes())
    return buffer.getvalue()


def norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def truth() -> list[tuple[str, str]]:
    """(word, speaker) for every word of the transcript."""
    out: list[tuple[str, str]] = []
    for line in TRANSCRIPT.read_text().splitlines():
        name, said = line.split(":", 1)
        out += [(norm(w), name.strip()) for w in said.split() if norm(w)]
    return out


def score(segments: list[Segment]) -> dict[str, object]:
    heard = [(norm(w), s.speaker) for s in segments for w in s.text.split() if norm(w)]
    expected = truth()
    matcher = difflib.SequenceMatcher(
        a=[w for w, _ in heard], b=[w for w, _ in expected], autojunk=False
    )
    pairs = [
        (heard[block.a + k][1], expected[block.b + k][1])
        for block in matcher.get_matching_blocks()
        for k in range(block.size)
    ]
    by_label: dict[str | None, Counter[str]] = defaultdict(Counter)
    for label, person in pairs:
        by_label[label][person] += 1
    named = sum(c.most_common(1)[0][1] for label, c in by_label.items() if label is not None)
    cells = sorted(
        ((n, label, person) for label, c in by_label.items() if label for person, n in c.items()),
        reverse=True,
    )
    used_labels: set[str] = set()
    used_people: set[str] = set()
    one_to_one = 0
    for n, label, person in cells:
        if label not in used_labels and person not in used_people:
            used_labels.add(label)
            used_people.add(person)
            one_to_one += n
    return {
        "words_aligned": len(pairs),
        "words_heard": len(heard),
        "labels": len([label for label in by_label if label]),
        "people": len({p for _, p in expected}),
        "right_after_naming_each_label": round(named / len(pairs), 3),
        "right_one_to_one": round(one_to_one / len(pairs), 3),
        "unlabelled_words": sum(c.total() for label, c in by_label.items() if label is None),
        "segments": len(segments),
        "labels_by_person": {
            label: dict(c.most_common()) for label, c in sorted(by_label.items(), key=str)
        },
    }


def sysctl(name: str) -> str:
    """macOS only; elsewhere hardware() falls back to what Python knows."""
    return subprocess.run(  # noqa: S603 (a fixed program and our own arguments)
        ["/usr/sbin/sysctl", "-n", name], capture_output=True, text=True, check=True
    ).stdout.strip()


def hardware() -> str:
    try:
        chip, cores = sysctl("machdep.cpu.brand_string"), sysctl("hw.ncpu")
        memory = int(sysctl("hw.memsize"))
        return (
            f"{chip}, {cores} cores, {memory // 2**30} GB, {platform.system()} {platform.machine()}"
        )
    except (OSError, subprocess.CalledProcessError, ValueError):
        return f"{platform.processor()} {platform.machine()}"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=Settings().diarization_threshold)
    parser.add_argument("--shift", type=float, default=Settings().diarization_window_shift)
    parser.add_argument("--whisper", default="base.en")
    args = parser.parse_args()
    data = AUDIO.read_bytes()
    diarizer = SherpaOnnxDiarizer(
        diarization_model_dir(Settings()), threshold=args.threshold, window_shift=args.shift
    )
    whisper = FasterWhisperTranscriber(args.whisper)

    # Downloading and loading the models is not timed (the pipeline keeps them loaded).
    started = time.perf_counter()
    await asyncio.to_thread(diarizer._load)
    loaded = {"diarization_models_ready_s": round(time.perf_counter() - started, 1)}
    await asyncio.to_thread(whisper._load)

    # One after the other, then both at once (as the pipeline runs them), twice each. Other
    # programs share the CPU: the load average before and after is part of the result.
    load_before = [round(x, 1) for x in os.getloadavg()]
    runs: list[dict[str, float]] = []
    for _ in range(2):
        started = time.perf_counter()
        transcript = await whisper.transcribe(data, AUDIO.name)
        transcribed = time.perf_counter() - started
        turns = await diarizer.diarize(data, AUDIO.name)
        sequential = time.perf_counter() - started
        started = time.perf_counter()
        together = await asyncio.gather(
            whisper.transcribe(data, AUDIO.name), diarizer.diarize(data, AUDIO.name)
        )
        runs.append(
            {
                "transcribe_s": round(transcribed, 1),
                "diarize_s": round(sequential - transcribed, 1),
                "one_after_the_other_s": round(sequential, 1),
                "both_at_once_s": round(time.perf_counter() - started, 1),
            }
        )

    load_after = [round(x, 1) for x in os.getloadavg()]

    # The threshold by meeting length: the recording's first 2, 5 and 10 minutes, and all of it,
    # labelled on the full transcription's segments that start in that stretch.
    sweep: dict[str, dict[str, object]] = {}
    samples = decode_audio(data, RATE)
    for seconds in (*CUTS, int(transcript.duration or 0) + 1):
        clip = wav(samples[: seconds * RATE])
        heard = [s for s in transcript.segments if s.start < seconds]
        for threshold in THRESHOLDS:
            cut = SherpaOnnxDiarizer(
                diarization_model_dir(Settings()), threshold=threshold, window_shift=args.shift
            )
            scored = score(assign_speakers(heard, await cut.diarize(clip, "clip.wav")))
            sweep[f"{min(seconds, round(transcript.duration or 0))} s @ {threshold}"] = {
                k: scored[k]
                for k in ("labels", "right_after_naming_each_label", "right_one_to_one")
            }

    labelled = assign_speakers(transcript.segments, turns)
    same_again = assign_speakers(together[0].segments, together[1]) == labelled
    segments = [
        {"id": f"T{i}", "start": s.start, "end": s.end, "text": s.text}
        for i, s in enumerate(labelled, start=1)
    ]
    outline = word_outline(segments)
    expected = next(i for i in load_meetings(ROOT / "evals" / "datasets") if i.id == "mt-01")
    missed = score_topics(expected.topics, outline, segments)
    print(
        json.dumps(
            {
                "recording": AUDIO.name,
                "duration_s": round(transcript.duration or 0, 1),
                "hardware": hardware(),
                "whisper": args.whisper,
                "diarizer": diarizer.model_id,
                "threshold": args.threshold,
                "window_shift": args.shift,
                "speakers_found": len({t.speaker for t in turns}),
                "turns": len(turns),
                **loaded,
                "load_average_1_5_15_min": {"before": load_before, "after": load_after},
                "runs": runs,
                "accuracy": score(labelled),
                "threshold_by_length": sweep,
                "same_labels_on_rerun": same_again,
                "word_outline": [(t["title"], round(t["start"])) for t in outline],
                "word_outline_topics_found": f"{len(expected.topics) - len(missed)}/"
                f"{len(expected.topics)}",
                "word_outline_topics_missed": missed,
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
