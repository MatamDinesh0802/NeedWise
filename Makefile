# WorkMind AI developer commands. Run `make` to list them.
.DEFAULT_GOAL := help
COMPOSE := docker compose
INFRA := postgres valkey qdrant openfga keycloak caddy
HTTP_PORT ?= 8480

.PHONY: help objects-dir up down reset logs ps status infra api-dev web-dev install lint fmt test e2e e2e-browser seed authz-model embeddings ee-dev-license docs docs-serve docs-gen docs-lock loadtest loadtest-stub loadtest-report loadtest-sweep loadtest-check backup restore upgrade-test helm-check helm-kind-test

help: ## List available commands
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

quickstart: ## First run on any OS: embeddings in Docker, the full stack, the Acme demo data
	@test -f .env || cp .env.example .env
	@grep -q '^EMBEDDING_BASE_URL=' .env || echo 'EMBEDDING_BASE_URL=http://embeddings:8080/v1' >> .env
	@grep -q '^COMPOSE_PROFILES=' .env || echo 'COMPOSE_PROFILES=embeddings' >> .env
	@$(MAKE) --no-print-directory objects-dir
	$(COMPOSE) up -d --build --wait
	$(MAKE) seed
	@grep -qE '^(ANTHROPIC_API_KEY|LLM_PROVIDER)=' .env || \
		echo "\nSearch works now. For answers, add ANTHROPIC_API_KEY=... (or a local model) to .env, then: make up"

objects-dir: ## Create the bind-mounted directories; on Linux, object storage owned by uid 10001
	@mkdir -p .data/objects .workmind
	@# The containers run as uid 10001. Docker Desktop ignores ownership; a Linux daemon does not.
	@if [ "$$(uname -s)" = Linux ] && [ "$$(stat -c %u .data/objects)" != 10001 ]; then \
		docker run --rm -v "$(CURDIR)/.data/objects:/objects" alpine:3.22 chown -R 10001 /objects; \
	fi

up: ## Build and start the full stack, wait until every service is healthy
	@$(MAKE) --no-print-directory objects-dir
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
	@$(MAKE) --no-print-directory objects-dir
	API_UPSTREAM=host.docker.internal:8400 WEB_UPSTREAM=host.docker.internal:3400 \
		$(COMPOSE) up -d --wait $(INFRA)

api-dev: ## Run the API on the host with auto-reload (after `make infra`)
	cd apps/api && uv run alembic upgrade head && uv run uvicorn workmind.main:create_app --factory --reload --port 8400

web-dev: ## Run the web app on the host with hot reload (after `make infra`)
	cd apps/web && pnpm dev

install: ## Install backend and frontend dependencies
	cd apps/api && uv sync --extra ee --extra mysql
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
FGA := docker run --rm -v "$(CURDIR)/$(FGA_MODEL_DIR):/model" openfga/cli:v0.8.2@sha256:31a565d14d1c5ca8170d3a2225eb66517d9f490539967bcb3421613c5e2538a4

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

helm-kind-test: ## Install, test, upgrade and uninstall the chart on a throwaway kind cluster (needs kind, kubectl, helm)
	deploy/helm/kind-test.sh

# Browser suite (e2e/browser/README.md). E2E_MODEL=1 adds the @model tests (they call the model).
E2E_BASE_URL ?= http://localhost:$(HTTP_PORT)
e2e-browser: ## Browser end-to-end tests (Playwright, Chromium) against the running, seeded stack
	cd e2e/browser && pnpm install --frozen-lockfile && pnpm exec playwright install chromium
	cd e2e/browser && E2E_BASE_URL=$(E2E_BASE_URL) pnpm exec playwright test

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

# Supply-chain checks, the same as the Security workflow (docs/security/supply-chain.md). Each
# scanner runs from a container image pinned by digest. scan-images builds under separate tags
# (workmind-api:scan, workmind-web:scan), so the images `make up` runs are left alone.
.PHONY: scan scan-images
scan: ## Scan lockfiles, repository, Dockerfiles and workflows (Docker only, nothing installed)
	scripts/security-scan.sh all

scan-images: ## Build the api and web images under :scan tags, scan them, remove them
	docker build -t workmind-api:scan --build-context ee=ee apps/api
	docker build -t workmind-web:scan apps/web
	scripts/security-scan.sh image workmind-api:scan
	scripts/security-scan.sh image workmind-web:scan
	docker image rm workmind-api:scan workmind-web:scan

# Documentation site (mkdocs.yml, docs/): MkDocs with Material, run by uv from the pinned
# docs/requirements.txt, so nothing is installed globally.
DOCS_PYTHON := 3.13
DOCS_PORT ?= 8495
# docs-lock pins only packages released on or before this date: move it on purpose.
DOCS_LOCK_DATE ?= 2026-10-29
MKDOCS := uvx --python $(DOCS_PYTHON) --with-requirements docs/requirements.txt mkdocs

docs: ## Build the documentation site into site/ (strict: a broken link or anchor fails)
	$(MKDOCS) build --strict

docs-serve: ## Serve the documentation with live reload on http://127.0.0.1:8495
	$(MKDOCS) serve --dev-addr 127.0.0.1:$(DOCS_PORT)

docs-gen: ## Regenerate docs/reference/ (settings, OpenAPI) from the code; a test checks it
	cd apps/api && uv run python -m workmind.docs_gen

docs-lock: ## Re-pin docs/requirements.txt (with hashes) from docs/requirements.in
	uv pip compile docs/requirements.in --python-version $(DOCS_PYTHON) --generate-hashes \
		--exclude-newer $(DOCS_LOCK_DATE) --custom-compile-command "make docs-lock" \
		-o docs/requirements.txt

# Load tests (docs/load-testing.md): Locust in loadtests/, its own uv project. A run creates the
# demo users' access tokens, runs Locust headless, cleans up, revokes the tokens and writes
# loadtests/results/<date>-<LABEL>.md. Model scenarios need the stack pointed at the stub
# (make loadtest-stub; loadtests/compose.stub.yaml): the preflight stops the run otherwise.
# NO_MODEL=1 leaves out everything that calls the model.
#   make loadtest USERS=5 DURATION=60s NO_MODEL=1 LABEL=smoke
#   make loadtest USERS=50 SPAWN_RATE=5 DURATION=10m STUB_URL=http://127.0.0.1:8490 LABEL=stub-50
LOADTEST_HOST ?= http://localhost:$(HTTP_PORT)
USERS ?= 10
SPAWN_RATE ?= 1
DURATION ?= 2m
LABEL ?= run
STUB_PORT ?= 8490

loadtest: ## Load test (USERS SPAWN_RATE DURATION LABEL MIX NO_MODEL STUB_URL UPLOAD_COLLECTION MONITOR)
	cd loadtests && uv run python -m workmind_load.run --host $(LOADTEST_HOST) \
		--users $(USERS) --spawn-rate $(SPAWN_RATE) --duration $(DURATION) --label $(LABEL) \
		$(if $(MIX),--mix $(MIX)) $(if $(NO_MODEL),--no-model) $(if $(STUB_URL),--stub-url $(STUB_URL)) \
		$(if $(UPLOAD_COLLECTION),--upload-collection $(UPLOAD_COLLECTION)) $(if $(THINK),--think $(THINK)) \
		$(if $(PROCESSES),--processes $(PROCESSES)) $(if $(MONITOR),--monitor $(MONITOR))

loadtest-stub: ## Run the stub model server for load tests (STUB_PORT TTFT_MS TPS ANSWER_TOKENS)
	cd loadtests && uv run python stub_llm.py --port $(STUB_PORT) \
		$(if $(TTFT_MS),--ttft-ms $(TTFT_MS)) $(if $(TPS),--tokens-per-s $(TPS)) \
		$(if $(ANSWER_TOKENS),--answer-tokens $(ANSWER_TOKENS))

loadtest-report: ## Rewrite a load-test report from its raw stats (RUN=<date>-<label>)
	cd loadtests && uv run python -m workmind_load.report results/raw/$(RUN)/locust \
		--meta results/raw/$(RUN)/meta.json --out results/$(RUN).md

loadtest-sweep: ## Revoke leftover load-test tokens, delete leftover [load] conversations
	cd loadtests && uv run python -m workmind_load.tokens sweep --host $(LOADTEST_HOST) \
		$(if $(UPLOAD_COLLECTION),--upload-collection $(UPLOAD_COLLECTION))

loadtest-check: ## Lint, type-check and test the load-test harness (no stack needed)
	cd loadtests && uv run ruff check . && uv run ruff format --check . && uv run pyright \
		&& uv run pytest -q

# Operations (docs/operations/). Options: OUTPUT=<dir>, BACKUP_FLAGS="--offline" (or --online),
# RESTORE_FLAGS="--force --public-url https://workmind.example.com", FROM=<tag>.
backup: ## Back up the databases, Qdrant, objects and license to ./backups/workmind-<time>
	scripts/backup.sh $(if $(OUTPUT),--output "$(OUTPUT)") $(BACKUP_FLAGS)

restore: ## Restore a backup into this stack: make restore BACKUP=backups/workmind-<time>
	@test -n "$(BACKUP)" || { echo "usage: make restore BACKUP=<backup directory>"; exit 2; }
	scripts/restore.sh "$(BACKUP)" $(RESTORE_FLAGS)

upgrade-test: ## Upgrade a throwaway stack from an older release to this code: FROM=v0.1.0
	scripts/upgrade-test.sh $(or $(FROM),v0.1.0)
