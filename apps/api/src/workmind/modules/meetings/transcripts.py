"""Transcript files people already have: WebVTT (Teams, Zoom, Meet exports), SRT, and plain
"Speaker: text" lines. Audio and video go through transcription instead."""

import re
from pathlib import PurePath

from workmind.providers.transcription import Segment, Transcript

AUDIO = {".mp3", ".m4a", ".wav", ".mp4", ".webm", ".ogg", ".flac", ".aiff", ".aif", ".mov"}
TEXT = {".vtt", ".srt", ".txt", ".md"}
_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_CUE = re.compile(rf"({_TIME.pattern})\s*-->\s*({_TIME.pattern})")
_VOICE = re.compile(r"^<v(?:\.[^ >]+)?\s+([^>]+)>(.*?)(?:</v>)?$")
_SPEAKER = re.compile(r"^([A-Z][\w .'-]{0,40}):\s+(.+)$")


def kind(filename: str) -> str:
    suffix = PurePath(filename).suffix.lower()
    if suffix in AUDIO:
        return "audio"
    if suffix in TEXT:
        return "transcript"
    raise ValueError(f"unsupported meeting file type {suffix or '(none)'}")


def _seconds(match: re.Match[str], offset: int = 0) -> float:
    hours, minutes, seconds, fraction = (match.group(i + offset) for i in range(1, 5))
    return (
        int(hours or 0) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(fraction.ljust(3, "0")) / 1000
    )


def _speaker(text: str) -> tuple[str | None, str]:
    voice = _VOICE.match(text)
    if voice:
        return voice.group(1).strip(), voice.group(2).strip()
    named = _SPEAKER.match(text)
    if named:
        return named.group(1).strip(), named.group(2).strip()
    return None, text


def parse(data: bytes, filename: str) -> Transcript:
    text = data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
    blocks = [b for b in re.split(r"\n\s*\n", text) if "-->" in b]
    if blocks:  # VTT or SRT cues
        segments: list[Segment] = []
        for block in blocks:
            lines = [line.strip() for line in block.strip().splitlines()]
            cue = next((i for i, line in enumerate(lines) if "-->" in line), None)
            match = _CUE.search(lines[cue]) if cue is not None else None
            if match is None or cue is None:
                continue
            start, end = _seconds(match, 1), _seconds(match, 6)
            body = " ".join(line for line in lines[cue + 1 :] if line)
            speaker, said = _speaker(re.sub(r"</?(c|i|b|u)[^>]*>", "", body))
            if said:
                segments.append(Segment(start, end, said, speaker))
        return Transcript(segments, duration=segments[-1].end if segments else None)
    # Plain "Name: text" lines without timestamps: keep the order, approximate times by length.
    segments, clock = [], 0.0
    for line in (line.strip() for line in text.splitlines()):
        if not line:
            continue
        speaker, said = _speaker(line)
        length = max(2.0, len(said.split()) / 2.5)  # ~150 words per minute
        segments.append(Segment(clock, clock + length, said, speaker))
        clock += length
    return Transcript(segments, duration=clock or None)
