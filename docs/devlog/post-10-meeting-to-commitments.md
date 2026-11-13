# LinkedIn post #10: From a meeting recording to tracked commitments, with the audio on your own machine

- **Status:** Draft, not posted
- **Milestone:** Phase 9 (meeting intelligence)
- **Visual (suggested, not made yet):** the meeting page from the demo walkthrough (`docs/media/demo.mp4`, "a meeting's clickable timestamps"): decisions and action items, each with a timestamp, the player jumping to that moment. A still at 1080×1350 or the clip as native video. A second slide could show the name-hint table (`docs/meetings.md`): "Olivier" and "Way" without the hint.
- **Numbers from:** `docs/meetings.md` (transcription times, the name hint, diarization), `evals/results/2026-11-12-v1.0-meetings.md` (the v1.0 extraction run), `docs/benchmarks/v1.0.md` (cost per meeting).
- **Title note:** the roadmap's title was "From a 30-minute meeting to tracked commitments, fully self-hosted". Neither part is measured: the recording is 23 minutes, and the extraction ran on Claude, a hosted model (a local model can be configured; it was not measured). The post says what was measured.
- **Post only after** the repo is public.

---

## Post

Most meeting notes fail at the same point: who said they'd do what, by when.

So I tested exactly that. A 23-minute weekly operations meeting, five people, a fact plan listing what it must contain: 4 decisions, 5 action items with deadlines, plus distractors (small talk, and an idea everyone turned down).

What I built in WorkMind:
• Upload a recording or a transcript. Speech-to-text runs locally (faster-whisper), so the audio never leaves your server
• A model extracts decisions and action items. Every item links to the moment it was said, and timestamps are checked against the transcript: an item pointing at a moment that doesn't exist keeps its text but gets no timestamp, rather than a made-up one
• One click turns an action item into a tracked task. "My commitments" lists what you promised, across meetings
• The transcript is indexed, so later questions about the meeting get cited answers, with the same permissions as everything else

Measured on a laptop CPU (Apple M5): transcribing the 23 minutes took 42 to 51 seconds; 78 seconds in the Docker worker. Extraction took 9 seconds and cost about 3 cents with Claude Sonnet 5.5. From the recording: 4/4 decisions, 5/5 action items with the right owner and due date.

What didn't work at first: speech-to-text doesn't know your colleagues. Whisper wrote "Olivia" as "Olivier" and "Wei" as "Way". Without a speaker label, the model works out who owns "I'll update the SOP" from names said nearby, so a misheard name loses the owner. It missed the same action item in every run.

The fix was boring: pass the company directory's names to Whisper as hints. Action items linked to a real colleague went from 83–85% to 100%.

The caveats, because they matter: one synthetic meeting, read aloud by computer voices from a script. Speaker diarization put 95% of words under the right person once each speaker label was named, but on these voices, with a threshold chosen on this same recording. Expect worse in real rooms with crosstalk. And in the v1.0 run, one extracted item matched the idea the meeting turned down; the scoring can't yet tell whether it was stated as agreed.

What's the one thing your meeting notes always lose?

Repo and the meeting evaluation in the first comment.

#AI #MeetingNotes #SpeechToText #OpenSource #BuildInPublic

---

## First comment

Repo (Apache-2.0): <GITHUB_REPO_URL>
How it works and every measurement: <GITHUB_REPO_URL>/blob/main/docs/meetings.md
The v1.0 meetings run: <GITHUB_REPO_URL>/blob/main/evals/results/2026-11-12-v1.0-meetings.md
The name-hint experiment: <GITHUB_REPO_URL>/blob/main/evals/experiments/meeting_name_hint.py
