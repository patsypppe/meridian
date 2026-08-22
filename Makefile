.PHONY: help sync lint fmt typecheck unit integration e2e check clean sweep images db self-eval

help:
	@grep -E '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

sync: ## install dependencies from the lockfile
	uv sync

lint: ## ruff lint + format check
	uv run ruff check src tests benchmarks
	uv run ruff format --check src tests benchmarks

fmt: ## apply ruff formatting and autofixes
	uv run ruff check --fix src tests benchmarks
	uv run ruff format src tests benchmarks

typecheck: ## mypy --strict over src/meridian
	uv run mypy

unit: ## fast pure-logic tests
	uv run pytest -m unit

integration: ## Docker-backed tests
	uv run pytest -m integration

e2e: ## full run/replay/gate tests
	uv run pytest -m e2e

check: lint typecheck unit ## the gate every commit must pass

images: ## (re)build the environment and proxy images and repin the suite
	uv run meridian snapshot build ./envs/checkout --tag checkout:dev \
		--write-ref --update-suite ./suites/checkout-agent
	uv run meridian snapshot build ./envs/proxy --tag meridian-proxy:dev --context .

db: ## start Postgres and bring the schema up to head
	docker compose up -d postgres
	uv run alembic upgrade head

self-eval: ## measure Meridian with Meridian (slow: ~30 min of real gate runs)
	uv run python -m benchmarks.self_eval --out benchmarks/self-eval.json

sweep: ## remove leaked trial containers
	uv run meridian sweep

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
