# Meetings

A meeting recording or transcript becomes a summary, decisions, action items and an outline of
its topics. Each one links to the moment it was said. In a recording, voices are told apart, and
people with the right to can name them. Action items become tracked tasks, and the transcript
becomes searchable knowledge. Design:
[ARCHITECTURE section 15](ARCHITECTURE.md#15-meeting-intelligence).

## How it works

```
upload ──► transcribe ──────────► extract ──► topics ──────────► index
POST       faster-whisper; for    Claude      Claude, checked     transcript → a document
/meetings  a recording also       structured  against the         of the meeting's
           diarization, at the    output      transcript (or      collection, trust =
           same time (or parse                from its words)     discussion
           VTT/SRT/TXT)
```

- **Input:** recordings (mp3, m4a, wav, mp4, webm, ogg, flac, aiff, mov) or transcripts (VTT
  with `<v Name>` voice tags as exported by Zoom, Teams and Meet; SRT; plain `Name: text`
  lines). Uploads go up to `MAX_MEETING_BYTES` (500 MB).
- **Transcription:** `faster-whisper` runs locally on the worker's CPU (the `meetings` extra,
  in the Docker image). The model, `base.en` by default, downloads on the first recording into
  the `whisper-models` volume. `TRANSCRIPTION_PROVIDER=openai_compatible` sends audio to any
  `/v1/audio/transcriptions` server instead, and `none` accepts transcripts only.
- **Speakers:** while Whisper transcribes a recording, diarization works out who spoke when.
  [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) (Apache-2.0, the `diarization` extra, in
  the Docker image) runs pyannote's segmentation-3.0 model (MIT) to find speech and speaker
  changes, and NVIDIA NeMo TitaNet-S (under the NeMo toolkit's Apache-2.0 license) to tell voices
  apart, then clusters them. Both are ONNX files run on the CPU: no PyTorch, no Hugging Face
  account. They download on the first recording (47 MB, 46 MB unpacked) from sherpa-onnx's
  GitHub releases into `DIARIZATION_MODEL_DIR` (`/models/diarization` in the image) and are checked against
  SHA-256 digests pinned in `providers/diarization.py`. For an offline install, copy the two
  files there. Each transcript segment takes the speaker heard most while it was said, labelled
  `SPEAKER_1`, `SPEAKER_2`, … in order of first appearance (the app shows "Speaker 1"). Transcripts
  that name their speakers are not diarized. With `DIARIZATION_PROVIDER=none`, without the extra,
  or when diarization fails, the recording is still transcribed, without labels, and the meeting
  page says why (`timings.diarization_skipped`).
- **pyannote.audio** itself is not used: it needs PyTorch (several GB) and its pretrained
  pipelines are gated behind a Hugging Face token. A diarizer is anything with
  `diarize(data, filename) -> list[Turn]` (`providers/diarization.py`), so pyannote.audio or a
  hosted diarization service can take sherpa-onnx's place. None is included.
- **Extraction:** one structured-output call (the `generator` role) returns a summary,
  decisions and action items with owner and due date. Relative dates ("by Friday next week")
  are resolved against the meeting day: the date in the file name
  (`weekly-ops-sync-2026-11-02.txt`, as recorders and exports name files), or the upload day if
  the name has none. The app shows that day (`held_on`) for the meeting and its commitments;
  the upload time is in the tooltip. There is no field to enter it yet. The transcript is shown to the model as numbered
  segments `[T12] (04:31) Wei Zhang: …`. Each item must name the segment where it was said.
  That pointer is checked like an answer citation: an item pointing at a segment that does not
  exist keeps its text but gets no timestamp, rather than a made-up one. A diarized speaker
  nobody has named yet appears as its label: the model is told to give, as the owner of their
  commitment, the name the meeting calls them by, or else the label, which naming the speaker
  later resolves.
- **Topics:** a second structured call with the same role splits the meeting into the topics it
  covered, in order: a title and the segment where each starts. Those pointers are checked too
  (unknown segments and empty titles are dropped, topics are put in transcript order, the first
  starts at the first segment), and each topic's times are its segments' own, so an outline never
  has a made-up time. If the call fails or none of its topics points at a real segment, the
  outline is worked out from the transcript's words instead: about one topic per five minutes
  (at most eight), starting where the content words of the two minutes before and after have
  least in common, each titled with the three words that set it apart from the other topics
  ("Drill, Tuesday, Thursday"). The meeting page says when that happened
  (`timings.topics_fallback`).
- **Owners:** a name that identifies exactly one active colleague ("Wei" or "Wei Zhang") links
  the item to that person. Ambiguous names stay unlinked. Colleagues' names are also given to
  the transcriber as a spelling hint (measured below).
- **Index:** the transcript is uploaded to the meeting's collection as a Markdown document with
  `trust = discussion`, so answers that rely on it say it comes from a discussion and cite the
  5-minute section. It starts with the list of topics, and uses speakers' names once they are
  named.

## Who sees what

A meeting belongs to a collection. **Viewers** of that collection see the meeting and find its
transcript in search, under the same two permission locks as any document. **Editors** (and the
uploader) can upload, correct action items, turn them into tasks and delete the meeting. Others
get a 404. For a meeting only some people should see, upload it to a collection shared with just
them, such as `leadership-strategy`. The design in ARCHITECTURE section 15 has per-participant
visibility. Permissions follow the collection instead, so meetings never need a second
permission model.

**Naming speakers** is narrower, because it says who said what: the person who uploaded the
meeting, **attendees** (colleagues a speaker is already named as) who can edit it, and
organization owners and admins who can see it. An editor of the collection who was not in the
meeting cannot, until someone names them as a speaker. The page shows the section only to them
(`can_map_speakers`), and the API checks the same.

## API

| Endpoint | |
|---|---|
| `POST /meetings?collection=&filename=&title=` | Upload. Body: the raw file. 202, processed in the background |
| `GET /meetings` | Meetings in collections you can view |
| `GET /meetings/{id}` | Segments, speakers, summary, decisions, action items, topics, timings, `can_edit` and `can_map_speakers`. A diarization label the model wrote in the summary, a decision, an action item or a topic title is shown as the name it is mapped to (stored as extracted, so a later change of name applies too) |
| `GET /meetings/{id}/media` | The original recording, for playback from a timestamp. Answers `Range` requests with 206: Chromium only lets a player seek in media served that way |
| `PUT /meetings/{id}/speakers` | Name speakers: `{"speakers": {"SPEAKER_2": "<colleague's id>", "SPEAKER_3": {"name": "Jonas Brandt"}, "SPEAKER_4": null}}`, a colleague, a guest (linked to nobody) or back to the label. Action items owned by a renamed label name the colleague or guest as their owner (items that are tasks already keep theirs: change the task). The transcript document gets a new version with the names, which is indexed again. Audited as `meeting.speakers_mapped`, colleagues by id and guests only as `"guest"`. 403 for others (see above), 409 `not_ready` while processing, 422 for an unknown label or colleague or an empty guest name. Nothing changes and nothing is recorded when the names are the same |
| `PATCH /meetings/{id}/actions/{index}` | Correct an action item: `text`, `owner_user_id`, `due` (before it becomes a task) |
| `POST /meetings/{id}/actions/{index}/task` | Turn an action item into a task for its owner (who is notified). Once per item |
| `GET /me/commitments` | Action items assigned to you, with their task's status |
| `DELETE /meetings/{id}` | The uploader or the collection's editors (viewers get 403, others 404). Removes the meeting, its recording or transcript file (unless another meeting or document uses the same bytes), and its transcript document with its versions, chunks and search points. Tasks made from its action items stay with their assignees: their origin keeps the meeting's title (`meeting_title`, `meeting_deleted: true`) instead of a link, and the "notes ready" notification is removed. Audited as `meeting.deleted` and `knowledge.document.deleted`. 204 |

Status: `pending` → `transcribing` → `extracting` → `ready`, or `failed` with the reason, such as
transcription being off for a recording. Diarization runs during `transcribing` (it never fails
the meeting), the topic outline during `extracting`. The uploader is notified when the notes are
ready.

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
- **`/meetings/{id}`** shows the summary, the topics, decisions, action items and the
  transcript, with speakers' names. **Topics** is an outline: each topic is a button with its
  time range that plays the recording from there, or for a transcript scrolls to it and moves
  focus there; during playback the current topic is marked. The transcript shows where each
  topic starts. A decision or action item with a valid segment has a timestamp that does the
  same. Items without a valid segment have no timestamp, and a note says why. Editors (the
  uploader and the collection's editors) can correct an action item's text, owner or date, and
  make a task from it. After that, the item links to its task. They also see **Delete**, which
  asks first, naming the meeting and the tasks that will stay.
- **Who is who** (for those who may name speakers) lists each speaker with how many segments
  and minutes they spoke, the first thing they said, and a button to hear it (or see it in the
  transcript). Each can be named as a colleague from the directory, or as a guest by typing a
  name. Diarization tends to split a voice into two labels rather than merge two voices (see
  below): name both labels as the same person. A recording without labels says why on the
  transcript.
- **My commitments** is a section of `/tasks`: action items that name you as the owner, with the
  due date, the task's status and a link that opens the meeting at that moment (`?t=271.4`).
- **`/tasks/{id}`** shows one task (`GET /tasks/{id}`). A task made from a meeting links back to
  that meeting; once the meeting is deleted it reads "From “title” (deleted meeting)", without a
  link.

How the topic outline and "Who is who" were checked: unit tests for their logic (the topic at a
moment, the naming payload with guests, the speaker summaries) and for the rendered page (the
outline's buttons and labels, the naming form, the note on an unlabelled recording), API tests
with a scripted model (naming, permissions, the relabelled notes, the new transcript version and
its chunks, the audit event), and one run in headless Chromium. That run used this branch's API
on a throwaway database and the Next.js dev server, with a scripted model, transcriber and
diarizer, on the first 40 seconds of the demo recording as WAV. Its 23 checks passed: a topic
button moved the recording to the topic's start (22.3 s; the player read 22.7 s), Enter on a
focused topic worked, a speaker's button played their first words, a guest without a name was
refused, naming one speaker as a colleague and another as a guest relabelled the transcript, the
summary and the action item's owner, a viewer saw the outline but not "Who is who", and the page
had no horizontal scroll at 390 px. axe (WCAG 2.2 A/AA and best practices) found no serious or
critical issue on the page before and after naming, and in the dark theme at 390 px, apart from
the account button that the browser suite already excludes. The run first found three issues,
fixed since: the outline buttons' names did not start with their visible text, the speaker
selects had labels that contradicted the value they showed (they now have visible labels), and
timestamps on a highlighted transcript row lacked contrast. The run is a script outside the
repository's browser suite: that suite runs against the seeded stack, whose meetings need a real
model to be processed.

How the rest was checked: unit tests for the logic (timestamps, jump targets, the correction payload,
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
deterministic, extraction is not), without diarization: the eval transcribes recordings with
Whisper alone. With diarization (now on by default) the model sees `SPEAKER_n` labels; that,
and the one line about them added to the extraction prompt, has not been measured with a real
model. After the change to how structured-output schemas are built
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

**Speaker diarization.** The demo recording (five synthetic voices, 23 minutes) through Whisper
and diarization, on the Apple M5 (10 cores, 24 GB) while other programs used it (load average 7
to 10). Ground truth: the recording was made line by line from the transcript, so every word's
speaker is known; Whisper's words are aligned to the transcript's, and a word counts as right
when the label of its segment is named as its speaker. "After naming each label" names every
label as the person most of its words belong to, as someone would in "Who is who"; "one label
per person" allows each person one label only
([experiment](../evals/experiments/meeting_diarization.py),
[results](../evals/results/2026-11-13-meeting-diarization.jsonl)):

| Default settings (threshold 0.6, window step 0.5) | Demo recording |
|---|---|
| Labels found (5 people) | 7: two small extra labels (123 and 4 words) |
| Words with the right speaker, after naming each label | 95.3% (3,271 words aligned) |
| Words with the right speaker, one label per person | 91.9% |
| Most frequent mix-up | 71 of Hannah's words under Olivia's label (both female voices) |
| Transcription alone, base.en | 54 s, 63 s |
| Diarization alone | 21 s, 27 s |
| Both at once, as the pipeline runs them | 72 s, 85 s (one after the other: 75 s, 90 s) |
| Same labels on a second run | yes |
| Models (once) | a 47 MB download; loading took 0.1 s with the files present |

The threshold decides how many voices there are, and the right value depended on the meeting's
length. Same recording, its first 2, 5 and 10 minutes and all of it (all five people speak in
the first 2 minutes), words right after naming each label (labels found):

| Threshold | 2 min | 5 min | 10 min | 23 min |
|---|---|---|---|---|
| 0.5 | 94.1% (7) | 93.6% (6) | 95.0% (8) | 96.4% (10) |
| **0.6 (default)** | 87.9% (5) | 93.1% (5) | 95.0% (6) | 95.3% (6) |
| 0.7 | 76.7% (3) | 71.0% (3) | 86.1% (4) | 95.3% (6) |
| 0.8 | 76.7% (3) | 71.0% (3) | 75.0% (3) | 95.2% (5) |
| 0.9 | 61.3% (2) | 71.0% (3) | 75.0% (3) | 94.9% (5) |

0.8 and 0.9 found exactly the five people on the full recording but merged voices in every
shorter cut, and a merged voice cannot be fixed by naming speakers; a split one can (name both
labels). 0.6 is the highest threshold that found at least five labels in every cut. The sweep
re-encodes each cut as WAV, and on the full recording that gave 6 labels where the m4a gave 7:
the clustering is sensitive to small differences in the audio. All of this is one synthetic meeting made with
macOS voices, and the threshold was chosen on it, so expect worse on real rooms, crosstalk and
similar voices.

Tried and dropped, on the same recording: splitting segments at Whisper's word times, so one
segment never holds two speakers, put fewer words right (93.9% against 95.6% labelling whole
segments, threshold 0.9, an earlier version of the experiment) and made transcription 14% slower.
In exploratory runs whose scripts were not kept: pyannote's own window step (0.1) took 90 s
instead of about 20 s for 3.6 points more at threshold 0.9; 3D-Speaker CAM++ embeddings put
61 to 75% of words right with 7 to 31 labels, and WeSpeaker ResNet34 put everyone under one label
at thresholds 0.8 and 0.9; 8 threads were no faster than 1.

**Topic outlines.** The meetings eval scores them: each expected topic has title keywords and an
anchor, words said well inside it, and counts when the outline's topic covering the anchor has a
fitting title. The model-based outline has not been run with a real model yet (no model key
where this was built): `wm eval --suites meetings` measures it. The deterministic fallback, with
no model at all, found 4/4 topics of the operations sync from its transcript and from its
Whisper transcription (titles such as "Packs, battery, Blufen", "Claim, answer, hours",
"Drill, Tuesday, Thursday"), and 1/2 of the 2-minute support sync, which it keeps as one topic.
Its stop words and window were adjusted while looking at these two transcripts, so those scores
are optimistic.

## Not built yet

- Telling the diarizer how many people spoke (sherpa-onnx can take a count; in an exploratory
  run on the demo recording's 2-, 5- and 10-minute cuts, a known count of 5 did no better than
  the default threshold, so it is not offered), and recognizing a returning voice across
  meetings.
- Topic outlines and extraction from diarized recordings measured with a real model.
- Open questions and risks in the extraction; DOCX transcripts.
- Zoom, Teams and Meet connectors, and checking later meetings for progress on commitments.
