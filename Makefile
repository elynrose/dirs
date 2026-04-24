.PHONY: up down ps logs migrate api worker kill-api restart-local web-dev web-deploy tenant-admin electron electron-pack

SHELL := /bin/bash

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
DOTENV := $(ROOT)/.env

# PEP 668 / Homebrew: use apps/api/.venv (create: cd apps/api && python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]").
API_DIR := $(ROOT)/apps/api
WEB_DIR := $(ROOT)/apps/web
# Production static root for nginx (see scripts/nginx/directely.com.conf). Override: make web-deploy WEB_DEPLOY_ROOT=/other/path
WEB_DEPLOY_ROOT ?= /var/www/directely
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

web-deploy:
	@test -d "$(WEB_DEPLOY_ROOT)" || { echo "web-deploy: WEB_DEPLOY_ROOT does not exist: $(WEB_DEPLOY_ROOT)"; exit 1; }
	cd "$(WEB_DIR)" && rm -rf node_modules/.vite && npm run build
	rsync -a --delete "$(WEB_DIR)/dist/" "$(WEB_DEPLOY_ROOT)/"
	nginx -s reload
	@echo "web-deploy: synced dist/ → $(WEB_DEPLOY_ROOT) (nginx reloaded)"

# Grant Studio / admin API workspace admin: make tenant-admin EMAIL=user@host
# Optional: TENANT_ID=<uuid> DRY_RUN=1
tenant-admin:
	@test -n "$(EMAIL)" || { echo "usage: make tenant-admin EMAIL=user@example.com [TENANT_ID=uuid] [DRY_RUN=1]"; exit 1; }
	cd "$(API_DIR)" && $(REPO_ENV) && .venv/bin/python "$(ROOT)/scripts/make_tenant_admin.py" "$(EMAIL)"$(if $(TENANT_ID), --tenant-id $(TENANT_ID),)$(if $(DRY_RUN), --dry-run,)

ELECTRON_DIR := $(ROOT)/apps/electron

electron:
	cd "$(ELECTRON_DIR)" && npm install && npm start

electron-pack:
	cd "$(ELECTRON_DIR)" && npm install && npm run pack
