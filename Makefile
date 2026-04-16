.PHONY: help install bootstrap up down logs ps restart migrate test lint fmt clean shell-pg shell-redis

# Default target
help:
	@echo "HERALD — Make targets"
	@echo "  install     uv sync (local dev deps)"
	@echo "  bootstrap   Create DBs/users/extensions and run migrations"
	@echo "  up          docker compose up -d (all services)"
	@echo "  down        docker compose down"
	@echo "  logs        Tail logs for all services (follow)"
	@echo "  ps          docker compose ps"
	@echo "  restart     Restart the app services (poller/extractor/mcp)"
	@echo "  migrate     Run alembic migrations against Postgres target"
	@echo "  test        Run pytest"
	@echo "  lint        Run ruff check"
	@echo "  fmt         Run ruff format"
	@echo "  shell-pg    psql into bundled postgres"
	@echo "  shell-redis redis-cli into bundled redis"

install:
	uv sync --all-extras

bootstrap:
	./scripts/bootstrap_db.sh

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

ps:
	docker compose ps

restart:
	docker compose restart feed_poller extractor mcp_server

migrate:
	uv run alembic -x db=postgres upgrade head

migrate-timescale:
	uv run alembic -x db=timescale upgrade head

test:
	uv run pytest -v

lint:
	uv run ruff check .

fmt:
	uv run ruff format .

shell-pg:
	docker compose exec postgres psql -U $${POSTGRES_USER:-herald} -d $${POSTGRES_DB:-herald}

shell-redis:
	docker compose exec redis redis-cli

clean:
	docker compose down -v
