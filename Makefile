.PHONY: up down logs migrate ingest ingest-date seed analytics test lint psql reset help
.DEFAULT_GOAL := help

DAYS ?= 30
COMPOSE := docker compose

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

up: ## Start postgres + api
	$(COMPOSE) up -d --build

down: ## Stop everything
	$(COMPOSE) down

logs: ## Tail container logs
	$(COMPOSE) logs -f api db

migrate: ## Apply pending migrations
	$(COMPOSE) run --rm api python scripts/migrate.py

ingest: ## Fetch and load the last $(DAYS) trading days from NSE
	$(COMPOSE) run --rm api python -m slbdesk.ingest --days $(DAYS)

ingest-date: ## Fetch and load one date: DATE=2026-09-18 make ingest-date
	$(COMPOSE) run --rm api python -m slbdesk.ingest --date $(DATE)

seed: ## Generate the synthetic book
	$(COMPOSE) run --rm api python scripts/seed_book.py

analytics: ## Refresh derived tables, then run validation queries
	$(COMPOSE) run --rm api python scripts/refresh_analytics.py

test: ## Run pytest
	pytest -q

lint: ## Lint and format check
	ruff check . && ruff format --check .

psql: ## Open a database shell
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-slbdesk} -d $${POSTGRES_DB:-slbdesk}

reset: ## Drop and recreate the database (asks first)
	@read -p "Drop the database and all data? [y/N] " a; [ "$$a" = "y" ] || exit 1
	$(COMPOSE) down -v && $(COMPOSE) up -d db
