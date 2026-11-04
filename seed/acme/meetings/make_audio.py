"""Rebuild the demo meeting recording from its transcript (macOS: `say` and ffmpeg).

    python3 seed/acme/meetings/make_audio.py

One voice per participant (from weekly-ops-sync.plan.yaml), 120 words per minute, half a second
between turns, AAC at 48 kbit/s mono. Voices differ between macOS versions, so a rebuilt file is
close to the committed one but not identical.
"""

import re
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
TRANSCRIPT = HERE / "weekly-ops-sync-2026-11-02.txt"
OUTPUT = HERE / "weekly-ops-sync-2026-11-02.m4a"


def voices() -> dict[str, str]:
    """name -> voice, read from the plan without needing PyYAML."""
    plan = (HERE / "weekly-ops-sync.plan.yaml").read_text()
    pattern = r"\{ name: ([^,]+?),\s+role: .*?voice: (\w+) \}"
    return {name.strip(): voice for name, voice in re.findall(pattern, plan)}


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


def main() -> None:
    cast = voices()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        gap = work / "gap.wav"
        ffmpeg("-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "0.5", str(gap))
        listing: list[str] = []
        for i, line in enumerate(TRANSCRIPT.read_text().splitlines()):
            name, text = line.split(":", 1)
            spoken, wav = work / f"{i:03d}.aiff", work / f"{i:03d}.wav"
            subprocess.run(
                ["say", "-v", cast[name], "-r", "120", "-o", str(spoken), text.strip()], check=True
            )
            ffmpeg("-i", str(spoken), "-ar", "22050", "-ac", "1", str(wav))
            listing += [f"file '{wav}'", f"file '{gap}'"]
        (work / "list.txt").write_text("\n".join(listing) + "\n")
        ffmpeg("-f", "concat", "-safe", "0", "-i", str(work / "list.txt"), "-c:a", "aac",
               "-b:a", "48k", str(OUTPUT))  # fmt: skip
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
