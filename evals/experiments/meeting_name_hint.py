"""A/B: Whisper with and without the directory-name hint. Transcription is deterministic
(beam 1), so names are compared once; extraction is graded over 3 runs per arm."""
import asyncio, json, re, sys
from datetime import date
from pathlib import Path
from needwise.core.config import Settings
from needwise.core.resources import Resources
from needwise.core.tenancy import tenant_transaction
from needwise.evals.datasets import load_meetings, keywords_match
from needwise.evals.runner import Askers
from needwise.modules.meetings.extract import extract
from needwise.modules.meetings.pipeline import load_directory

ROOT = Path(__file__).resolve().parents[2]  # run from apps/api: uv run python ../../evals/experiments/meeting_name_hint.py
NAMES = {"Olivia": r"\bOlivia\b", "Wei": r"\bWei\b", "Samuel": r"\bSamuel\b", "Dan": r"\bDan\b", "Hannah": r"\bHannah\b"}
WRONG = r"\b(Olivier|Olivea|Way|Wai|Why|Samual|Hanna|Hana|Danny)\b"

def grade(item, out):
    decisions = [d["text"] for d in out["decisions"]]
    dec = sum(any(keywords_match(g, d) for d in decisions) for g in item.decisions)
    act = 0; missed = []
    for e in item.actions:
        ok = any(e["owner"].lower() in a["owner"].lower() and keywords_match(e["keywords"], a["text"]) for a in out["action_items"])
        act += ok
        if not ok: missed.append(e["owner"])
    linked = sum(a["owner_user_id"] is not None for a in out["action_items"]) / max(1, len(out["action_items"]))
    return dec, act, missed, linked

async def main():
    r = Resources.create(Settings())
    a = Askers(r, "acme"); tenant = await a.load_tenant()
    async with tenant_transaction(r.db, tenant.id) as s:
        directory = await load_directory(s)
    item = load_meetings(ROOT / "evals" / "datasets")[0]
    audio = ROOT / "seed/acme/meetings/weekly-ops-sync-2026-11-02.m4a"
    for arm, vocab in (("no hint", ()), ("hint", directory.names)):
        t = await r.transcriber.transcribe(audio.read_bytes(), audio.name, vocabulary=vocab)
        text = " ".join(s.text for s in t.segments)
        right = {n: len(re.findall(p, text)) for n, p in NAMES.items()}
        wrong = re.findall(WRONG, text)
        segs = [{"id": f"T{i}", "start": s.start, "end": s.end, "speaker": None, "text": s.text} for i, s in enumerate(t.segments, 1)]
        runs = []
        for _ in range(3):
            out, _u = await extract(r.llm, segs, organization=tenant.name, day=date(2026, 11, 2), speakers={}, people=directory.people)
            runs.append(grade(item, out))
        print(json.dumps({"arm": arm, "names_right": right, "names_wrong": wrong,
                          "decisions": [x[0] for x in runs], "actions": [x[1] for x in runs],
                          "missed": [x[2] for x in runs], "owner_linked": [round(x[3], 2) for x in runs]}))
    await r.aclose()
asyncio.run(main())
