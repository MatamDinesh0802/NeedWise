# Local models

NeedWise uses models for three different jobs. Each can run on your own hardware:

| Job | Default | Local by default? |
|---|---|---|
| **Embeddings** (indexing and search) | EmbeddingGemma-300M on llama.cpp, any `/v1/embeddings` server | Yes |
| **Language model** (answers, planning, agents, meeting notes, Insights, evaluation) | Claude Sonnet 5.5 through the Anthropic API | No: set it up below |
| **Speech to text** (meeting recordings) | faster-whisper `base.en` on the worker's CPU | Yes |

So with the defaults, document text is embedded locally and recordings are transcribed
locally; what goes to Anthropic is what the language model needs to answer: the question and
the passages the person may read. The design is in
[ADR 0007](../adr/0007-model-providers-and-lightweight-parsing.md).

## A fully local language model

Any server with an OpenAI-compatible `/v1/chat/completions` endpoint works: Ollama, vLLM,
llama.cpp (`llama-server`), LM Studio. In `.env`:

```bash
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=http://host.docker.internal:11434/v1   # Ollama on the host
LLM_MODEL=qwen3:14b
# LLM_API_KEY=...                                  # only if the server needs one
```

Then `make up`. `host.docker.internal` is how the containers reach a server on your machine
(the API, worker and seed containers have it mapped on every OS).

- **On a Mac, run the model server natively**, not in Docker: containers on macOS cannot use the
  Apple GPU. Keep Docker Desktop's memory limit around 8 GB and leave the rest for the model.
- **One model for everything, or one per job.** `LLM_MODEL` is the default for every role;
  `LLM_MODEL_ANSWER`, `LLM_MODEL_ROUTER`, `LLM_MODEL_AGENT`, `LLM_MODEL_GENERATOR` and
  `LLM_MODEL_JUDGE` override one each. For example a small fast model for `router`
  (rewriting follow-up questions, planning) and a larger one for `answer`.
- **Local or external.** An `openai_compatible` server counts as **local** for the data policy
  unless you set `LLM_EXTERNAL=true`. Set it when `LLM_BASE_URL` is a hosted API (OpenAI, a
  gateway): otherwise restricted documents could be sent there.
- **Per organization.** Instead of (or besides) the deployment's model, each organization can
  add its own providers in **Admin › Models**, mark the ones running on its own infrastructure
  as local, and pick a model per role ([Models and keys](../models.md)).

## Keep restricted documents local

A common setup is a hosted model for most answers and a local one for the most sensitive
documents. Documents carry a sensitivity label (`internal`, `confidential`, `restricted`), and
each organization's data policy says the highest label an external model may receive. New
organizations allow up to `confidential`.

```bash
ANTHROPIC_API_KEY=<your Anthropic API key>
LOCAL_LLM_BASE_URL=http://host.docker.internal:11434/v1
LOCAL_LLM_MODEL=qwen3:14b
```

When an answer needs a source above the threshold, the whole request goes to the local model
instead. Without a local model, those sources are left out and the person is told how many were
withheld and why. This happens after both permission checks, so it never widens what anyone can
read. Measured on the demo company with no local model configured: the 6 questions whose
answers exist only in restricted documents were answered without them, and none of those
documents reached the hosted model
([report](../evals/results/2026-11-04-local-only.md)).

What the policy does not cover yet (tool results in research mode, Insights query results,
meeting transcripts sent for extraction) is listed in
[Models and keys](../models.md#the-data-policy-what-may-leave).

## Embeddings

The embedding model turns passages and questions into vectors for search. Two ways to run the
default, EmbeddingGemma-300M:

| | How | When |
|---|---|---|
| In Docker (CPU) | `make quickstart`, or `COMPOSE_PROFILES=embeddings` and `EMBEDDING_BASE_URL=http://embeddings:8080/v1` | Any OS |
| On the host | `make embeddings` (needs llama.cpp installed) and `EMBEDDING_BASE_URL=http://host.docker.internal:8091/v1` | macOS: uses the Apple GPU, faster |

Both serve the same model file and produce the same vectors, so you can switch between them
without re-indexing.

**Another embedding model** (`EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, `EMBEDDING_BASE_URL`)
starts a new search index, named after the model and its dimensions. Re-index each
organization with `nw reindex <tenant>`. The relevance floor (`RETRIEVAL_MIN_SIMILARITY`) and the
agents' fast-path thresholds are calibrated for EmbeddingGemma: check them with
`nw eval --retrieval-only` before relying on another model.

## Speech to text

Meeting recordings are transcribed by faster-whisper on the worker's CPU, about 25 times faster
than real time with `base.en` (a 23-minute recording in under a minute on a laptop). The model
downloads on the first recording. `TRANSCRIPTION_MODEL=small.en` or `medium.en` is slower and
more accurate. `TRANSCRIPTION_PROVIDER=openai_compatible` with `TRANSCRIPTION_BASE_URL` sends
audio to any `/v1/audio/transcriptions` server instead, and `none` accepts transcripts only
([Meetings](../meetings.md)).

## What has been measured

The published evaluation runs answers with Claude Sonnet 5.5 and Claude Opus 5.5, and
embeddings with EmbeddingGemma-300M ([Evaluation](../evals/README.md)). **No evaluation with a
local language model has been published yet.** Local models differ widely in how well they
follow the citation rules and call tools. Before relying on one, run the same suites against
it on the demo company:

```bash
cd apps/api
uv run nw eval --judge     # answers graded by a judge model (LLM_MODEL_JUDGE)
```
