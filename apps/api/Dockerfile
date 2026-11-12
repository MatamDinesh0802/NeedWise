# syntax=docker/dockerfile:1
# WorkMind API image: FastAPI app + Alembic migrations.
# It also holds the Enterprise package (ee/python, commercial license), so one image runs either
# edition: without a valid license the core never imports it (docs/enterprise.md). ee/ comes in
# as the named build context "ee"; compose passes it, by hand:
#   docker build --build-context ee=ee apps/api

FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

FROM base AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.17 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
# The repository layout, so the ee extra's path (../../ee/python) resolves. Only /app/.venv
# (no editable installs) goes into the runtime image.
WORKDIR /build/apps/api
# Dependencies first, for layer caching.
COPY pyproject.toml uv.lock ./
COPY --from=ee python/pyproject.toml python/LICENSE /build/ee/python/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --no-install-package workmind-ee \
        --extra meetings --extra ee
COPY src ./src
COPY --from=ee python/src /build/ee/python/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --extra meetings --extra ee

FROM base AS runtime
RUN useradd --system --uid 10001 --no-create-home workmind \
    && mkdir -p /models && chown workmind /models  # speech models download here (a volume)
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
ENV PATH="/app/.venv/bin:$PATH" \
    LOG_FORMAT=json \
    HF_HOME=/models
USER workmind
EXPOSE 8000
CMD ["uvicorn", "workmind.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
