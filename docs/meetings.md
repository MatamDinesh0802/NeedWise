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
  are resolved against the meeting day: the date in the file name
  (`weekly-ops-sync-2026-11-02.txt`, as recorders and exports name files), or the upload day if
  the name has none. The app shows that day (`held_on`) for the meeting and its commitments;
  the upload time is in the tooltip. There is no field to enter it yet. The transcript is shown to the model as numbered
  segments `[T12] (04:31) Wei Zhang: …`. Each item must name the segment where it was said.
  That pointer is checked like an answer citation: an item pointing at a segment that does not
  exist keeps its text but gets no timestamp, rather than a made-up one.
- **Owners:** a name that identifies exactly one active colleague ("Wei" or "Wei Zhang") links
  the item to that person. Ambiguous names stay unlinked. Colleagues' names are also given to
  the transcriber as a spelling hint (measured below).
- **Index:** the transcript is uploaded to the meeting's collection as a Markdown document with
  `trust = discussion`, so answers that rely on it say it comes from a discussion and cite the
  5-minute section.

## Who sees what

A meeting belongs to a collection. **Viewers** of that collection see the meeting and find its
transcript in search, under the same two permission locks as any document. **Editors** (and the
uploader) can upload, map speakers, turn action items into tasks and delete the meeting. Others
get a 404. For a
meeting only some people should see, upload it to a collection shared with just them, such as
`leadership-strategy`. The design in ARCHITECTURE section 15 has per-participant visibility.
Permissions follow the collection instead, so meetings never need a second permission model.

## API

| Endpoint | |
|---|---|
| `POST /meetings?collection=&filename=&title=` | Upload. Body: the raw file. 202, processed in the background |
| `GET /meetings` | Meetings in collections you can view |
| `GET /meetings/{id}` | Segments, speakers, summary, decisions, action items, timings |
| `GET /meetings/{id}/media` | The original recording, for playback from a timestamp. Answers `Range` requests with 206: Chromium only lets a player seek in media served that way |
| `PUT /meetings/{id}/speakers` | Map speaker labels to colleagues. Action items owned by that label are re-linked, and name the colleague as their owner (mapping back to nobody restores the label) |
| `PATCH /meetings/{id}/actions/{index}` | Correct an action item: `text`, `owner_user_id`, `due` (before it becomes a task) |
| `POST /meetings/{id}/actions/{index}/task` | Turn an action item into a task for its owner (who is notified). Once per item |
| `GET /me/commitments` | Action items assigned to you, with their task's status |
| `DELETE /meetings/{id}` | The uploader or the collection's editors (viewers get 403, others 404). Removes the meeting, its recording or transcript file (unless another meeting or document uses the same bytes), and its transcript document with its versions, chunks and search points. Tasks made from its action items stay with their assignees: their origin keeps the meeting's title (`meeting_title`, `meeting_deleted: true`) instead of a link, and the "notes ready" notification is removed. Audited as `meeting.deleted` and `knowledge.document.deleted`. 204 |

Status: `pending` → `transcribing` → `extracting` → `ready`, or `failed` with the reason, such as
transcription being off for a recording. The uploader is notified when the notes are ready.

A meeting can be deleted while it is still being processed. Processing stops at its next step,
and the transcript document is recorded on the meeting in the same locked transaction that
creates it, so the deletion either happens first (no document is made) or finds the document and
deletes it too.

## In the app

- **`/meetings`** lists the meetings in collections you can view, and polls every 3 seconds while
  one is being processed. Uploading needs edit access to a collection. The form shows the consent
  notice ("Everyone in the recording should know it is being processed") and won't upload until
  you confirm it. Uploads show their progress and can be cancelled. A failed meeting shows its
  reason in the list and on its page.
- **`/meetings/{id}`** shows the summary, decisions, action items and the transcript, with
  speakers' names. A decision or action item with a valid segment has a timestamp. For a
  recording, the timestamp plays the recording from that moment. For a transcript, it scrolls the
  transcript to that segment and moves focus there. Items without a valid segment have no
  timestamp, and a note says why. Editors (the uploader and the collection's editors) can map
  speakers to colleagues, correct an action item's text, owner or date, and make a task from it.
  After that, the item links to its task. They also see **Delete**, which asks first, naming the
  meeting and the tasks that will stay.
- **My commitments** is a section of `/tasks`: action items that name you as the owner, with the
  due date, the task's status and a link that opens the meeting at that moment (`?t=271.4`).
- **`/tasks/{id}`** shows one task (`GET /tasks/{id}`). A task made from a meeting links back to
  that meeting; once the meeting is deleted it reads "From “title” (deleted meeting)", without a
  link.

How it was checked: unit tests for the logic (timestamps, jump targets, the correction payload,
sorting commitments) and for the rendered detail view, plus one end-to-end run in headless
Chromium. That run used this branch's API on a throwaway database and the Next.js dev server, with
a scripted model and a transcriber that returns the demo transcript. It made 47 checks, all
passing: upload, status polling, timestamps, transcript scrolling and focus, corrections, a task
made once (409 the second time), My commitments opening the meeting at its moment, editors versus
viewers, a 404 for outsiders, and a failed recording. For seeking, the 23-minute demo recording was
converted to WAV (Playwright's Chromium has no AAC decoder). A click on the 8:36 timestamp put the
player at 516.0 s. When the same file was served without Range support, as the endpoint did before,
the same browser reported it seekable from 0 to 0 s, and setting the time to 516 s left the player
at 0. The run is not yet an automated test in CI, and it did not use a real model.

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
| Decisions found | 4/4 | 4/4, 3/4, 4/4 |
| Ideas nobody agreed to, reported as decided | 0 | 0 |
| Action items, right owner and task | 5/5 | 5/5, 4/5, 5/5 |
| Action items linked to a colleague | 100% | 100% |
| Items with a valid timestamp | 100% | 100% |
| Transcription (base.en, int8) | none | 42 to 51 s for 23 min on the Apple M5 CPU; 78 s in the Docker worker (134 s on its first run, which downloads the model) |
| Extraction (Claude Sonnet 5.5) | 9 s | 9 s |

Recording results are three extraction runs on one transcription (Whisper with beam 1 is
deterministic, extraction is not). After the change to how structured-output schemas are built
(fields named `title` are kept), the transcript run was repeated on 2026-11-12 with the same
result: 4/4 decisions, 5/5 action items, 5/5 due dates, 10 s
([results](../evals/results/2026-11-12-meetings-regression.md)).

**Colleagues' names as a spelling hint.** Without speaker labels, the model works out who owns
"I'll do it" from names said nearby, so a misheard name loses the owner. Whisper spelled
"Olivia" as "Olivier" and "Wei" as "Way". The transcriber now passes the company directory's
names to Whisper (`hotwords`, which primes every 30-second window). Same recording, three
extraction runs per arm ([experiment](../evals/experiments/meeting_name_hint.py),
[results](../evals/results/2026-11-04-meeting-name-hint.jsonl)):

| | No hint | Directory names as hint |
|---|---|---|
| "Wei" transcribed correctly | 2 times | 8 times |
| Misspelled names | "Olivier", "Way" | "Way" (twice) |
| Action items found (of 5) | 4, 4, 4 | 5, 4, 5 |
| Action items linked to a colleague | 83 to 85% | 100% |

Without the hint, the item missed every time was the chair's own "I'll update the SOP": the
model gave it to "Olivier", who is nobody. Editors can also correct an owner, wording or date by
hand (`PATCH /meetings/{id}/actions/{index}`) before an item becomes a task.

## Not built yet

- **Speaker diarization** (pyannote in the design). It needs PyTorch, several GB, and a gated
  model behind a Hugging Face token, too heavy for the default image. Recordings therefore have
  no speaker labels, and owners come from names said in the meeting (above). Transcripts from
  meeting tools already name their speakers. For the same reason, "Who is who" (speaker
  mapping) only appears for transcripts with named speakers. A recording has no labels to map, so
  its owners are corrected item by item.
- Topic segmentation, open questions and risks in the extraction; DOCX transcripts.
- Zoom, Teams and Meet connectors, and checking later meetings for progress on commitments.
