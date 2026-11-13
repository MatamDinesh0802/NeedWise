"""Speakers in a meeting: diarization's turns put on the transcript, and what naming a speaker
(SPEAKER_2 is Wei Zhang, or a guest) changes.

Diarization says who spoke when; speech to text says what was said when. Each transcript segment
gets the speaker heard most while it was said. Labels are numbered in order of first appearance:
SPEAKER_1, SPEAKER_2, ...

A mapping names a label. The transcript shows the name, the indexed transcript is rewritten with
it, and wherever the model wrote a label (in the summary, decisions, action items or topic
titles), the name is shown instead. Action items owned by the label name its colleague or guest.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from workmind.modules.meetings.extract import speaker_name
from workmind.providers.diarization import Turn
from workmind.providers.transcription import Segment

DIARIZED = re.compile(r"\bSPEAKER_\d+\b")
REACH = 1.0  # seconds: speech in a gap between turns goes to the nearest turn this close


def speaker_at(turns: Sequence[Turn], start: float, end: float) -> int | None:
    """The speaker heard most between `start` and `end`; in a gap between turns, the nearest
    turn within REACH seconds; else None."""
    heard: dict[int, float] = {}
    for turn in turns:
        overlap = min(end, turn.end) - max(start, turn.start)
        if overlap > 0:
            heard[turn.speaker] = heard.get(turn.speaker, 0.0) + overlap
    if heard:
        return max(sorted(heard), key=lambda speaker: heard[speaker])  # a tie: the lower number
    middle = (start + end) / 2
    distance, nearest = min(
        ((max(t.start - middle, middle - t.end, 0.0), t.speaker) for t in turns),
        default=(REACH + 1, None),
    )
    return nearest if distance <= REACH else None


def assign_speakers(segments: Sequence[Segment], turns: Sequence[Turn]) -> list[Segment]:
    """The transcript with speaker labels from diarization's turns (unchanged without turns).
    Each segment is labelled whole: splitting segments at Whisper's word times was tried, and
    put fewer words with the right speaker on the demo recording (docs/meetings.md)."""
    if not turns:
        return list(segments)
    labels: dict[int, str] = {}
    out: list[Segment] = []
    for segment in segments:
        speaker = speaker_at(turns, segment.start, segment.end)
        if speaker is not None and speaker not in labels:
            labels[speaker] = f"SPEAKER_{len(labels) + 1}"
        out.append(replace(segment, speaker=labels[speaker] if speaker is not None else None))
    return out


def relabel(text: str, speakers: Mapping[str, Any]) -> str:
    """`text` with each diarization label the model wrote (SPEAKER_2) shown as its name."""
    return DIARIZED.sub(lambda match: speaker_name(speakers, match.group(0)), text)


def relink_owners(
    items: Sequence[Mapping[str, Any]],
    changed: Mapping[str, Mapping[str, Any]],
    previous: Mapping[str, str],
) -> tuple[list[dict[str, Any]], int]:
    """Action items owned by a label whose mapping changed now name its colleague (linked) or
    guest (not linked), and how many did. The owner may be written as the label, as the label's
    new name, or as its previous name (an item an earlier mapping renamed). An item that is a
    task already keeps its owner: the task is changed instead."""
    out: list[dict[str, Any]] = []
    relinked = 0
    for item in items:
        updated = dict(item)
        owner = str(updated.get("owner") or "").strip().lower()
        if owner and not updated.get("task_id"):
            for label, mapped in changed.items():
                names = {label, str(mapped.get("name") or ""), previous.get(label, "")}
                if owner in {name.lower() for name in names if name}:
                    updated["owner_user_id"] = mapped.get("user_id")
                    updated["owner"] = mapped.get("name") or label
                    relinked += 1
                    break
        out.append(updated)
    return out, relinked
