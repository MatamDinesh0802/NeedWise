# syntax=docker/dockerfile:1
# WorkMind API image: FastAPI app + Alembic migrations.
# It also holds the Enterprise package (ee/python, commercial license), so one image runs either
# edition: without a valid license the core never imports it (docs/enterprise.md). ee/ comes in
# as the named build context "ee"; compose passes it, by hand:
#   docker build --build-context ee=ee apps/api
#
# Base images are pinned by digest (the tag is for people); Dependabot proposes new digests.
# docs/security/supply-chain.md explains the scans this image goes through.

# uv as a stage of its own, so Dependabot sees its FROM line too.
FROM ghcr.io/astral-sh/uv:0.11.17@sha256:03bdc89bb9798628846e60c3a9ad19006c8c3c724ccd2985a33145c039a0577b AS uv

FROM python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
# Debian security fixes published after the base image was built (the pinned digest can lag
# behind them by weeks), and no pip: the app runs from a uv-built virtualenv, and pip's vendored
# packages (urllib3, msgpack, setuptools) only add findings to the image scan.
RUN apt-get update \
    && apt-get upgrade --yes --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall --yes --root-user-action=ignore pip

FROM base AS builder
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
# The repository layout, so the ee extra's path (../../ee/python) resolves. Only /app/.venv
# (no editable installs) goes into the runtime image.
WORKDIR /build/apps/api
# Dependencies first, for layer caching.
COPY pyproject.toml uv.lock ./
# "ee" is a named build context, not a stage, which hadolint cannot know.
# hadolint ignore=DL3022
COPY --from=ee python/pyproject.toml python/LICENSE /build/ee/python/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --no-install-package workmind-ee \
        --extra meetings --extra ee
COPY src ./src
# hadolint ignore=DL3022
COPY --from=ee python/src /build/ee/python/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --extra meetings --extra ee

FROM base AS runtime
# /models: speech models download here. /var/lib/celerybeat: beat's schedule. Both are volumes.
RUN groupadd --system --gid 10001 workmind \
    && useradd --system --uid 10001 --gid 10001 --no-create-home workmind \
    && mkdir -p /models /var/lib/celerybeat && chown workmind:workmind /models /var/lib/celerybeat
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
ENV PATH="/app/.venv/bin:$PATH" \
    LOG_FORMAT=json \
    HF_HOME=/models
# Numeric, so Kubernetes' runAsNonRoot can verify it without reading /etc/passwd. No HEALTHCHECK
# here: the same image runs the API, workers, beat and migrations; compose checks each service.
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "workmind.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
