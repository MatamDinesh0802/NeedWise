# 0007. Model providers: Claude by default, local embeddings, swappable adapters; lightweight parsing

- Status: Accepted
- Date: 2026-11-04
- Supersedes: the LiteLLM and Docling choices in ARCHITECTURE section 2

## Context
The maintainer develops on an Apple M5 laptop with 24 GB of RAM, alongside other projects.
Running a local LLM there competes for memory; a hosted model gives far better answers per GB.
A small local embedding model, however, is cheap and fast on the Apple GPU, and keeps document
text local while indexing. Other adopters will have different hardware and policies.

## Decision
- **LLM:** our own `LLMProvider` interface with two implementations:
  `AnthropicLLM` (official Anthropic SDK, the default) and `OpenAICompatibleLLM` (any
  `/v1/chat/completions` server: Ollama, vLLM, llama.cpp, LM Studio). LiteLLM is not used: two thin
  adapters are less code to trust than a gateway library.
- **Roles:** callers ask for a role (`answer`, `router`, `judge`, `generator`). Every role defaults to
  `LLM_MODEL` (`claude-sonnet-5-5` since 2026-11-04, see the update below); `LLM_MODEL_<ROLE>`
  overrides one. Server-side refusal fallback
  (`fallbacks: "default"`) is on for models that support it.
- **Embeddings:** `EmbeddingProvider` with `OpenAICompatibleEmbeddings` (default: EmbeddingGemma-300M
  served by `llama-server --embeddings` on the host, Metal-accelerated, via `make embeddings`) and
  `HashEmbeddings` (deterministic, for tests and CI).
- **Parsing:** pure-Python parsers (pypdf, python-docx, openpyxl, python-pptx, stdlib HTML/JSON/CSV)
  behind one `parse()` function. Docling stays a drop-in option for layout-heavy PDFs; it adds about
  2 GB of ML dependencies to the worker image.

## Consequences
- Answers need an API key (or a local model server). Seeding and search do not.
- Anyone can run fully local: set `LLM_PROVIDER=openai_compatible` and `LLM_BASE_URL`.
- Scanned PDFs need OCR, which the lightweight parsers do not do; they fail with a clear reason.
- Changing the embedding model creates a new Qdrant collection (`chunks_<model>_<dims>`) and
  requires re-indexing, as ARCHITECTURE section 8 describes.

## Update (2026-11-04): Claude Sonnet 5.5 is the default

The default moved from Claude Opus 5.5 to Claude Sonnet 5.5 (half the price per token) at the
maintainer's request. Sonnet 5.5's effort levels are recalibrated, so the per-role efforts were
reset to its recommended starting points and the full evaluation was re-run
(`evals/results/2026-11-04-sonnet-baseline.md`). Opus 5.5 remains one setting away
(`LLM_MODEL=claude-opus-5-5`, or `LLM_MODEL_ANSWER` for answers only).
