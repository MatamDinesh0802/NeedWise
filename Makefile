# WorkMind AI developer commands. Run `make` to list them.
.DEFAULT_GOAL := help
COMPOSE := docker compose
INFRA := postgres valkey qdrant openfga keycloak caddy
HTTP_PORT ?= 8480

.PHONY: help up down reset logs ps status infra api-dev web-dev install lint fmt test seed

help: ## List available commands
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

up: ## Build and start the full stack, wait until every service is healthy
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

infra: ## Start only infrastructure, to run api/web on the host
	$(COMPOSE) up -d --wait $(INFRA)

api-dev: ## Run the API on the host with auto-reload (after `make infra`)
	cd apps/api && uv run alembic upgrade head && uv run uvicorn workmind.main:create_app --factory --reload --port 8400

web-dev: ## Run the web app on the host with hot reload (after `make infra`)
	cd apps/web && pnpm dev

install: ## Install backend and frontend dependencies
	cd apps/api && uv sync
	cd apps/web && pnpm install

lint: ## Lint and type-check everything
	cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run pyright
	cd apps/web && pnpm lint && pnpm typecheck

fmt: ## Auto-format backend code
	cd apps/api && uv run ruff format . && uv run ruff check --fix .

TEST_DATABASE_ADMIN_URL ?= postgresql://postgres:postgres@localhost:5432/postgres

test: ## Run all tests (integration tests need `make infra` or `make up`)
	cd apps/api && TEST_DATABASE_ADMIN_URL=$(TEST_DATABASE_ADMIN_URL) uv run pytest
	cd apps/web && pnpm test

seed: ## Seed the Acme Robotics demo company (arrives in Phase 1)
	@echo "Seeding arrives in Phase 1. See seed/README.md"
