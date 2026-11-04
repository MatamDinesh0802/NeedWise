# Meetings

A meeting recording or transcript becomes a summary, decisions and action items. Each one
links to the moment it was said. Action items become tracked tasks, and the transcript becomes
searchable knowledge. Design: [ARCHITECTURE section 15](ARCHITECTURE.md#15-meeting-intelligence).

## How it works

```
upload (recording or transcript) ──► transcribe ──► extract ──► index
  POST /api/v1/meetings               faster-whisper   Claude       transcript → a document of
  ?collection=…&filename=…            on the worker    structured   the meeting's collection,
                                      (or parse VTT/   output       5-minute sections,
                                      SRT/TXT)                      trust = discussion
```

- **Input:** recordings (mp3, m4a, wav, mp4, webm, ogg, flac, aiff, mov) or transcripts (VTT
  with `<v Name>` voice tags as exported by Zoom, Teams and Meet; SRT; plain `Name: text`
  lines). Uploads go up to `MAX_MEETING_BYTES` (500 MB).
- **Transcription:** `faster-whisper` runs locally on the worker's CPU (the `meetings` extra,
  in the Docker image). The model, `base.en` by default, downloads on the first recording into
  the `whisper-models` volume. `TRANSCRIPTION_PROVIDER=openai_compatible` sends audio to any
  `/v1/audio/transcriptions` server instead, and `none` accepts transcripts only.
- **Extraction:** one structured-output call (the `generator` role) returns a summary,
  decisions and action items with owner and due date. Relative dates ("by Friday next week")
  are resolved against the meeting day. The transcript is shown to the model as numbered
  segments `[T12] (04:31) Wei Zhang: …`. Each item must name the segment where it was said.
  That pointer is checked like an answer citation: an item pointing at a segment that does not
  exist keeps its text but gets no timestamp, rather than a made-up one.
- **Owners:** a name that identifies exactly one active colleague ("Wei" or "Wei Zhang") links
  the item to that person. Ambiguous names stay unlinked.
- **Index:** the transcript is uploaded to the meeting's collection as a Markdown document with
  `trust = discussion`, so answers that rely on it say it comes from a discussion and cite the
  5-minute section.

## Who sees what

A meeting belongs to a collection. **Viewers** of that collection see the meeting and find its
transcript in search, under the same two permission locks as any document. **Editors** (and the
uploader) can upload, map speakers and turn action items into tasks. Others get a 404. For a
meeting only some people should see, upload it to a collection shared with just them, such as
`leadership-strategy`. The design in ARCHITECTURE section 15 has per-participant visibility.
Permissions follow the collection instead, so meetings never need a second permission model.

## API

| Endpoint | |
|---|---|
| `POST /meetings?collection=&filename=&title=` | Upload. Body: the raw file. 202, processed in the background |
| `GET /meetings` | Meetings in collections you can view |
| `GET /meetings/{id}` | Segments, speakers, summary, decisions, action items, timings |
| `GET /meetings/{id}/media` | The original recording, for playback from a timestamp |
| `PUT /meetings/{id}/speakers` | Map speaker labels to colleagues. Action items owned by that label are re-linked |
| `POST /meetings/{id}/actions/{index}/task` | Turn an action item into a task for its owner (who is notified). Once per item |
| `GET /me/commitments` | Action items assigned to you, with their task's status |

Status: `pending` → `transcribing` → `extracting` → `ready`, or `failed` with the reason, such as
transcription being off for a recording. The uploader is notified when the notes are ready.

## Measured

`wm eval --suites meetings --meetings-audio`
([dataset](../evals/datasets/meetings.jsonl), [results](../evals/results/2026-11-04-meetings.md)).
The demo meeting is a synthetic weekly operations sync in `seed/acme/meetings/`, with five
participants, 3,445 words and a fact plan that lists what it must contain: 4 decisions, 5 action
items with deadlines, and distractors (an idea nobody agreed to, small talk). Claude wrote the
transcript from the plan. macOS `say` read it aloud with one voice per speaker, giving a
23-minute recording.

| | From the transcript | From the recording |
|---|---|---|
| Decisions found | 4/4 | 4/4 |
| Ideas nobody agreed to, reported as decided | 0 | 0 |
| Action items, right owner and task | 5/5 | 4/5 |
| Due dates resolved correctly | 5/5 | 4/5 |
| Items with a valid timestamp | 100% | 100% |
| Transcription (base.en, int8, Apple M5 CPU) | none | 42 to 51 s for 23 min |
| Extraction (Claude Sonnet 5.5) | 9 s | 9 s |

The one miss from the recording is the chair's own commitment ("And I'll update the SOP by
Thursday"). Whisper does not say who is speaking, so the model cannot tell whose "I'll" that is.

## Not built yet

- **Speaker diarization** (pyannote in the design). It needs PyTorch, several GB, and a gated
  model behind a Hugging Face token, too heavy for the default image. The cost is measured
  above. Transcripts from meeting tools already name speakers, and recordings can have speakers
  mapped after the fact.
- Topic segmentation, open questions and risks in the extraction; DOCX transcripts.
- Zoom, Teams and Meet connectors, and checking later meetings for progress on commitments.
