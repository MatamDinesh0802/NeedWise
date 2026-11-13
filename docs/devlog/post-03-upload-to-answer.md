# LinkedIn post #3: What happens between "upload PDF" and an AI answer

- **Status:** Draft, not posted
- **Milestone:** Phase 2 (knowledge ingestion)
- **Image:** `assets/post-03-ingestion-pipeline.png` (1080×1350). Every step is real code in `apps/api/src/needwise/modules/knowledge/`; the numbers come from `nw reindex acme` on the maintainer's laptop.
- **Post only after** the repo is public.

---

## Post

"Just upload your PDFs and ask questions."

That sentence hides most of the work. This week NeedWise got its ingestion pipeline: the part between a file and an answer you can trust.

Six steps, and what each one taught me:

1. Read the file, and keep its structure.
PDF, Word, Excel, PowerPoint, Markdown, HTML, CSV, Slack exports. Headings and page numbers travel with the text, so an answer can later say "Leave Policy 2026 › 2.1 Duration, page 3".

2. Cut by structure, not by character count.
Tables were the hard part. A chunk that holds rows 40–60 of a salary table but not its header is useless. So tables split by rows, with the header repeated in every piece.

The lesson came from a crash: my local embedding server rejected a 541-token chunk because its context window is shared across 4 parallel slots (512 each). Token estimates now err on the high side.

3. Label sensitivity by data, not by topic.
My first scanner flagged the leave policy as "restricted" because it mentions medical certificates. Policies talk about sensitive topics; they rarely contain sensitive data. It now looks for valid IBANs, ID numbers, and pay next to an actual amount.

4. Embed locally.
A 300M-parameter model (EmbeddingGemma) on the laptop GPU, plus keyword vectors for exact codes like "INC-2026-031". Indexing never sends document text to a third party.

5. Permissions go on every chunk.
Each chunk carries the list of who may read it. Search filters on it before ranking anything. Change who a collection is shared with, and the index updates in the same request.

6. Versions swap without a gap.
Same file again: nothing happens. New version: the new chunks go live before the old ones disappear. A test caught a bug where re-indexing duplicated every chunk.

Real numbers: 34 documents became 294 chunks, and re-indexing all of them takes 8.6 seconds on a laptop.

Next: search quality, measured. I'll show how vector search, keyword search and "hybrid" really compare on the same questions.

What's the messiest document format your company relies on?

Repo link in the first comment.

#OpenSource #RAG #AIEngineering #BuildInPublic #LLM

---

## First comment

Repo (Apache-2.0): https://github.com/MatamDinesh0802/NeedWise
The pipeline: https://github.com/MatamDinesh0802/NeedWise/tree/main/apps/api/src/needwise/modules/knowledge
Try it with the fictional company: `make quickstart`
