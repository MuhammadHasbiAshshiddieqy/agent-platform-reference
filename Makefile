.DEFAULT_GOAL := help

COMPOSE := docker compose -f deploy/docker-compose.yml --env-file .env

# §27.2 — target set wajib. Targets whose milestone hasn't been built yet
# fail loudly and say so, rather than silently no-op-ing (a silent no-op
# would let `make demo` on a clean clone report false success).

.PHONY: up
up: ## docker compose up -d, wait for infra healthy (M0)
	$(COMPOSE) up -d
	@echo "Waiting for infra services to report healthy (up to 5 minutes)..."
	@i=0; \
	until [ -z "$$( $(COMPOSE) ps --format '{{.Health}}' | grep -v -E '^(healthy|)$$' )" ]; do \
		i=$$((i+1)); \
		if [ $$i -ge 150 ]; then \
			echo "Timed out waiting for services to become healthy:"; \
			$(COMPOSE) ps; \
			exit 1; \
		fi; \
		sleep 2; \
	done
	@$(COMPOSE) ps

.PHONY: up-eval
up-eval: ## up (M8's eval-service is a CLI tool run via `uv run`, not its own container — see services/eval/pyproject.toml)
	$(COMPOSE) up -d

.PHONY: down
down: ## stop containers, keep volumes
	$(COMPOSE) down

.PHONY: clean
clean: ## stop containers AND delete all volumes — irreversible, wipes local data
	$(COMPOSE) down -v

.PHONY: reset
reset: ## reset demo state (db + cache + mock) without rebuilding images
	@echo "make reset: not implemented until M5 (mock-business-api) exists."
	@exit 1

.PHONY: migrate
migrate: ## alembic upgrade head against the running postgres
	DATABASE_URL="postgresql+psycopg://agent:$$(grep ^POSTGRES_PASSWORD .env | cut -d= -f2)@localhost:5432/agent_platform" \
	APP_DB_PASSWORD="$$(grep ^APP_DB_PASSWORD .env | cut -d= -f2)" \
		uv run alembic -c migrations/alembic.ini upgrade head

.PHONY: seed
seed: ## load seed data (tenant/user/document/business-api state)
	@echo "make seed: seed/users.yaml exists for mock-idp/mock-business-api (M5/M5b) to read directly."
	@echo "No database writes needed yet — nothing in M0's schema stores user/role data (§22.3, ADR-009)."

.PHONY: ingest
ingest: ## run ingestion against seed/documents/
	@echo "make ingest: not implemented until M3 (services/ingestion) exists."
	@exit 1

.PHONY: demo
demo: ## python demo/run_demo.py
	@echo "make demo: not implemented until M1 (first demo step) exists. See §27.3 milestone->step map."
	@exit 1

.PHONY: demo-slow
demo-slow: ## demo --slow, for live presentation
	@echo "make demo-slow: not implemented until M1 exists."
	@exit 1

.PHONY: test
test: ## unit + integration tests across the whole repo
	uv run pytest packages/contracts/tests services/harness/tests services/gateway/tests services/ingestion/tests services/retrieval/tests services/mock-business-api/tests services/mock-idp/tests services/async-worker/tests services/eval/tests tests -v

.PHONY: test-security
test-security: ## ONLY tenant isolation / RLS / ACL / capability-leak tests
	uv run pytest tests/security -v

.PHONY: lint
lint: ## ruff + mypy + lint-imports (service boundary, §4.1) + provider-name leak check
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy packages/contracts/src services/harness/src services/gateway/src services/ingestion/src services/retrieval/src services/mock-business-api/src services/mock-idp/src services/async-worker/src services/eval/src
	uv run lint-imports
	@# §4.1 rule 3 / §5.4: provider names belong only in config/model-router/.
	@if grep -rEn "gemini/|anthropic/|openai/|ollama/" services/ packages/ 2>/dev/null; then \
		echo "ERROR: provider name leaked outside config/model-router/ (see lines above)"; \
		exit 1; \
	fi

# §13.7 — eval-service runs as a plain `uv run` CLI against the already-
# running stack (localhost ports), the same pattern tests/integration/
# already uses — never its own docker-compose service.
EVAL_ENV := DATABASE_URL="postgresql+asyncpg://agent_app:$$(grep ^APP_DB_PASSWORD .env | cut -d= -f2)@localhost:5432/agent_platform" \
	MODEL_ROUTER_KEY="$$(grep ^LITELLM_MASTER_KEY .env | cut -d= -f2)"

.PHONY: eval-smoke
eval-smoke: ## eval tier smoke (§13.6/§13.8) — run + gate against the live stack, < 5 min
	$(EVAL_ENV) uv run --package eval-service python -m eval_service.run --tier smoke --git-sha "$$(git rev-parse HEAD)"
	$(EVAL_ENV) uv run --package eval-service python -m eval_service.gate --tier smoke

.PHONY: eval-full
eval-full: ## eval tier full (§13.6/§13.8) — run + gate against the live stack, k=3, < 25 min
	$(EVAL_ENV) uv run --package eval-service python -m eval_service.run --tier full --git-sha "$$(git rev-parse HEAD)"
	$(EVAL_ENV) uv run --package eval-service python -m eval_service.gate --tier full

.PHONY: eval-report
eval-report: ## render the last eval-smoke run's report (markdown + JSON under reports/)
	$(EVAL_ENV) uv run --package eval-service python -m eval_service.report --tier smoke

# §13.9 — nightly-tier production trace sampling, run standalone (never
# from CI — this reads real Langfuse traffic, not the golden set).
# AGENT_ID overridable: `make eval-nightly-sample AGENT_ID=hr-assistant`.
AGENT_ID ?= hr-assistant
.PHONY: eval-nightly-sample
eval-nightly-sample: ## sample recent production traces from Langfuse, surface lowest-scoring N for human review (never auto-added to the golden set)
	$(EVAL_ENV) \
	LANGFUSE_PUBLIC_KEY="$$(grep ^LANGFUSE_INIT_PROJECT_PUBLIC_KEY .env | cut -d= -f2)" \
	LANGFUSE_SECRET_KEY="$$(grep ^LANGFUSE_INIT_PROJECT_SECRET_KEY .env | cut -d= -f2)" \
		uv run --package eval-service python -m eval_service.nightly_sample --agent-id $(AGENT_ID)

.PHONY: logs
logs: ## follow logs for all running services
	$(COMPOSE) logs -f

.PHONY: ps
ps: ## service status + health
	$(COMPOSE) ps -a

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
