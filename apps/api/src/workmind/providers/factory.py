"""Builds the configured providers from settings."""

import httpx

from workmind.core.config import Settings
from workmind.providers.embeddings import (
    EmbeddingProvider,
    HashEmbeddings,
    OpenAICompatibleEmbeddings,
)
from workmind.providers.llm import (
    AnthropicLLM,
    Effort,
    LLMProvider,
    OpenAICompatibleLLM,
    Role,
    RoleConfig,
)
from workmind.providers.transcription import (
    DisabledTranscriber,
    FasterWhisperTranscriber,
    OpenAICompatibleTranscriber,
    Transcriber,
)

# Effort per role, from Claude Sonnet 5.5's recommended starting points (its levels are
# recalibrated: `low` for chat, search, extraction and generation; `medium` for multistep tool
# use and for grading), then checked against `wm eval` (evals/README.md).
ROLE_EFFORT: dict[Role, Effort] = {
    "answer": "low",  # grounded answers from given sources
    "router": "low",  # planning and question rewriting: quick classification
    "judge": "medium",  # faithfulness grading needs care
    "generator": "low",  # extraction (meeting notes) and generation
    "agent": "medium",  # department and research agents: multistep tool use
}


def role_configs(settings: Settings) -> dict[Role, RoleConfig]:
    overrides: dict[Role, str | None] = {
        "answer": settings.llm_model_answer,
        "router": settings.llm_model_router,
        "judge": settings.llm_model_judge,
        "generator": settings.llm_model_generator,
        "agent": settings.llm_model_agent,
    }
    return {
        role: RoleConfig(model=overrides[role] or settings.llm_model, effort=ROLE_EFFORT[role])
        for role in ROLE_EFFORT
    }


def secret(value: object) -> str | None:
    """The secret's value, or None when unset or empty (compose passes unset variables as "")."""
    getter = getattr(value, "get_secret_value", None)
    return (getter() or None) if getter else None


def create_llm(settings: Settings, http: httpx.AsyncClient) -> LLMProvider:
    roles = role_configs(settings)
    if settings.llm_provider == "anthropic":
        return AnthropicLLM(
            roles, api_key=secret(settings.anthropic_api_key), fallbacks=settings.llm_fallbacks
        )
    return OpenAICompatibleLLM(
        http,
        roles,
        base_url=settings.llm_base_url,
        api_key=secret(settings.llm_api_key),
        external=settings.llm_external,
    )


def create_local_llm(settings: Settings, http: httpx.AsyncClient) -> LLMProvider | None:
    """The model restricted sources may be sent to under a `local_only` policy, if configured."""
    if not (settings.local_llm_base_url and settings.local_llm_model):
        return None
    model = settings.local_llm_model
    return OpenAICompatibleLLM(
        http,
        {role: RoleConfig(model=model, effort=ROLE_EFFORT[role]) for role in ROLE_EFFORT},
        base_url=settings.local_llm_base_url,
        api_key=secret(settings.local_llm_api_key),
    )


def create_transcriber(settings: Settings, http: httpx.AsyncClient) -> Transcriber:
    match settings.transcription_provider:
        case "faster_whisper":
            return FasterWhisperTranscriber(settings.transcription_model)
        case "openai_compatible":
            return OpenAICompatibleTranscriber(
                http,
                base_url=settings.transcription_base_url,
                model=settings.transcription_model,
                api_key=secret(settings.transcription_api_key),
            )
        case _:
            return DisabledTranscriber()


def create_embeddings(settings: Settings, http: httpx.AsyncClient) -> EmbeddingProvider:
    if settings.embedding_provider == "hash":
        return HashEmbeddings(settings.embedding_dimensions)
    return OpenAICompatibleEmbeddings(
        http,
        base_url=settings.embedding_base_url,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        api_key=secret(settings.embedding_api_key),
        query_template=settings.embedding_query_template,
        document_template=settings.embedding_document_template,
    )
