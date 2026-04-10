.PHONY: up down ps logs migrate api worker kill-api restart-local web-dev electron electron-pack

SHELL := /bin/bash

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
DOTENV := $(ROOT)/.env

# PEP 668 / Homebrew: use apps/api/.venv (create: cd apps/api && python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]").
API_DIR := $(ROOT)/apps/api
WEB_DIR := $(ROOT)/apps/web
# Load repo-root .env (absolute path). Use `{ }` (same shell), not `( )`, so exports persist.
REPO_ENV := { set -a; [ -f "$(DOTENV)" ] && . "$(DOTENV)" || true; set +a; }

up:
	cd "$(ROOT)" && docker compose up -d
	@echo "Postgres: localhost:5433  Redis: localhost:6379  MinIO: http://localhost:9000 (console :9001)"

down:
	cd "$(ROOT)" && docker compose down

ps:
	cd "$(ROOT)" && docker compose ps

logs:
	cd "$(ROOT)" && docker compose logs -f

migrate:
	cd "$(API_DIR)" && $(REPO_ENV) && .venv/bin/alembic upgrade head

# Free API_PORT (from .env or default 8000) so a leftover uvicorn does not block `make api`.
kill-api:
	@{ set -a; [ -f "$(DOTENV)" ] && . "$(DOTENV)" || true; set +a; } && \
	P=$${API_PORT:-8000}; \
	pids=$$(lsof -tiTCP:$$P -sTCP:LISTEN 2>/dev/null || true); \
	if [ -n "$$pids" ]; then echo "kill-api: freeing port $$P (PID $$pids)"; kill -9 $$pids 2>/dev/null || true; fi

api: kill-api
	cd "$(API_DIR)" && $(REPO_ENV) && .venv/bin/python -m director_api

worker:
	cd "$(API_DIR)" && $(REPO_ENV) && .venv/bin/celery -A director_api.tasks.celery_app worker -l info

restart-local:
	bash "$(ROOT)/scripts/restart-local.sh"

web-dev:
	cd "$(WEB_DIR)" && npm install && npm run dev

ELECTRON_DIR := $(ROOT)/apps/electron

electron:
	cd "$(ELECTRON_DIR)" && npm install && npm start

electron-pack:
	cd "$(ELECTRON_DIR)" && npm install && npm run pack
