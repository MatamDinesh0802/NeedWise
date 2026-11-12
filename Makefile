# WorkMind AI developer commands. Run `make` to list them.
.DEFAULT_GOAL := help
COMPOSE := docker compose
INFRA := postgres valkey qdrant openfga keycloak caddy
HTTP_PORT ?= 8480

.PHONY: help up down reset logs ps status infra api-dev web-dev install lint fmt test e2e seed authz-model embeddings ee-dev-license

help: ## List available commands
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

quickstart: ## First run on any OS: embeddings in Docker, the full stack, the Acme demo data
	@test -f .env || cp .env.example .env
	@grep -q '^EMBEDDING_BASE_URL=' .env || echo 'EMBEDDING_BASE_URL=http://embeddings:8080/v1' >> .env
	@grep -q '^COMPOSE_PROFILES=' .env || echo 'COMPOSE_PROFILES=embeddings' >> .env
	@mkdir -p .data/objects .workmind
	$(COMPOSE) up -d --build --wait
	$(MAKE) seed
	@grep -qE '^(ANTHROPIC_API_KEY|LLM_PROVIDER)=' .env || \
		echo "\nSearch works now. For answers, add ANTHROPIC_API_KEY=... (or a local model) to .env, then: make up"

up: ## Build and start the full stack, wait until every service is healthy
	@mkdir -p .data/objects .workmind
	$(COMPOSE) up -d --build --wait
	@echo ""
	@echo "  WorkMind is running at http://localhost:$(HTTP_PORT)"
	@echo "  API docs: http://localhost:$(HTTP_PORT)/api/docs"

down: ## Stop the stack (data is kept)
	$(COMPOSE) down

reset: ## Stop the stack and DELETE all local data
	$(COMPOSE) down -v --remove-orphans

logs: ## Follow logs (one service: make logs s=api)
	$(COMPOSE) logs -f $(s)

ps: ## Show service status
	$(COMPOSE) ps -a

status: ## Show component health as reported by the API
	@curl -fsS http://localhost:$(HTTP_PORT)/api/v1/system/status | python3 -m json.tool

infra: ## Start infrastructure only; http://localhost:8480 then serves api-dev and web-dev
	@mkdir -p .data/objects
	API_UPSTREAM=host.docker.internal:8400 WEB_UPSTREAM=host.docker.internal:3400 \
		$(COMPOSE) up -d --wait $(INFRA)

api-dev: ## Run the API on the host with auto-reload (after `make infra`)
	cd apps/api && uv run alembic upgrade head && uv run uvicorn workmind.main:create_app --factory --reload --port 8400

web-dev: ## Run the web app on the host with hot reload (after `make infra`)
	cd apps/web && pnpm dev

install: ## Install backend and frontend dependencies
	cd apps/api && uv sync --extra ee
	cd apps/web && pnpm install

lint: ## Lint and type-check everything
	cd apps/api && uv run ruff check . ../../ee/python && uv run ruff format --check . ../../ee/python && uv run pyright
	cd apps/web && pnpm lint && pnpm typecheck

fmt: ## Auto-format backend code
	cd apps/api && uv run ruff format . ../../ee/python && uv run ruff check --fix . ../../ee/python

TEST_DATABASE_ADMIN_URL ?= postgresql://postgres:postgres@localhost:5432/postgres
TEST_VALKEY_URL ?= redis://localhost:6379/15
TEST_OPENFGA_URL ?= http://localhost:8401
TEST_OPENFGA_API_KEY ?= dev-only-openfga-key
TEST_QDRANT_URL ?= http://localhost:6333
FGA_MODEL_DIR := apps/api/src/workmind/modules/authz/openfga
FGA := docker run --rm -v "$(CURDIR)/$(FGA_MODEL_DIR):/model" openfga/cli:v0.8.2

test: ## Run unit + integration tests (integration needs `make infra` or `make up`)
	cd apps/api && TEST_DATABASE_ADMIN_URL=$(TEST_DATABASE_ADMIN_URL) \
		TEST_VALKEY_URL=$(TEST_VALKEY_URL) TEST_OPENFGA_URL=$(TEST_OPENFGA_URL) \
		TEST_OPENFGA_API_KEY=$(TEST_OPENFGA_API_KEY) TEST_QDRANT_URL=$(TEST_QDRANT_URL) uv run pytest
	cd apps/web && pnpm test

authz-model: ## Compile the OpenFGA model to JSON and run the authorization matrix
	$(FGA) model transform --file /model/model.fga > $(FGA_MODEL_DIR)/model.json
	$(FGA) model test --tests /model/model.fga.yaml

e2e: ## End-to-end tests against the running, seeded stack (`make up seed` first)
	cd apps/api && E2E_BASE_URL=http://localhost:$(HTTP_PORT) uv run pytest tests/e2e -v

helm-check: ## Lint, render and validate the Helm chart (needs helm and kubeconform on PATH)
	PYTHON="uv run --no-project --with pyyaml==6.0.2 python" deploy/helm/validate.sh

# Local embeddings: EmbeddingGemma-300M on llama.cpp (Apple GPU via Metal). 4 slots x 2048 tokens.
EMBED_GGUF ?= $(firstword $(wildcard $(HOME)/.cache/huggingface/hub/models--ggml-org--embeddinggemma-300M-GGUF/snapshots/*/embeddinggemma-300M-Q8_0.gguf))
EMBED_SOURCE = $(if $(EMBED_GGUF),-m "$(EMBED_GGUF)",-hf ggml-org/embeddinggemma-300M-GGUF:Q8_0)

embeddings: ## Run the local embedding model on :8091 (needs llama.cpp: brew install llama.cpp)
	llama-server $(EMBED_SOURCE) --embeddings --host 127.0.0.1 --port 8091 \
		-c 8192 --parallel 4 -ub 2048 -b 2048

# Enterprise edition, development only (docs/enterprise.md). Writes .workmind/ (git-ignored);
# then: docker compose restart api worker beat.  Options: FEATURES="a b" DAYS=30
ee-dev-license: ## Write a development Enterprise license to .workmind/ (APP_ENV=dev only)
	cd apps/api && uv run --extra ee wm license dev $(if $(FEATURES),--features $(FEATURES)) $(if $(DAYS),--days $(DAYS))

seed: ## Create the Acme Robotics demo company and its logins (safe to re-run)
	$(COMPOSE) --profile tools run --rm --build seed
