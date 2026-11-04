# syntax=docker/dockerfile:1
# WorkMind API image: FastAPI app + Alembic migrations.

FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

FROM base AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.17 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
# Dependencies first, for layer caching.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM base AS runtime
RUN useradd --system --uid 10001 --no-create-home workmind
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
ENV PATH="/app/.venv/bin:$PATH" \
    LOG_FORMAT=json
USER workmind
EXPOSE 8000
CMD ["uvicorn", "workmind.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
