.DEFAULT_GOAL := help

.PHONY: race help install migrate makemigrations superuser shell run test test-robustness \
        loadtest check-deploy frontend-install frontend-dev frontend-build frontend-preview

help:
	@echo "Backend (Django, via uv):"
	@echo "  make install         uv sync"
	@echo "  make run             runserver"
	@echo "  make migrate         apply migrations"
	@echo "  make makemigrations  generate migrations"
	@echo "  make superuser       createsuperuser"
	@echo "  make shell           Django shell"
	@echo "  make test            run tickets test suite"
	@echo "  make test-robustness bursts, hostile input, contention (Postgres for the races)"
	@echo "  make race            capacity + tier race tests, 10x (Postgres only)"
	@echo "  make loadtest        concurrent reads+writes against a local gunicorn --threads 4"
	@echo "  make check-deploy    manage.py check --deploy"
	@echo ""
	@echo "Frontend (Vite + Vue, in frontend/):"
	@echo "  make frontend-install"
	@echo "  make frontend-dev"
	@echo "  make frontend-build"
	@echo "  make frontend-preview"

install:
	uv sync

run:
	uv run python manage.py runserver

migrate:
	uv run python manage.py migrate

makemigrations:
	uv run python manage.py makemigrations

superuser:
	uv run python manage.py createsuperuser

shell:
	uv run python manage.py shell

test:
	uv run python manage.py test tickets

test-robustness:
	uv run python manage.py test tickets.test_robustness

# The contention evidence: ten consecutive runs of both threaded races -- capacity
# (nobody oversells) and tiers (nobody buys a cheap seat twice). One green run proves
# little, because losing a race is probabilistic.
#
# A SKIP is treated as a FAILURE. select_for_update() is a silent no-op on SQLite, so
# these tests skip there and a skipped run would otherwise read as success while
# proving nothing. Only meaningful with DATABASE_URL on Neon's DIRECT host.
RACE_TESTS = tickets.tests.CapacityRaceTest tickets.tests.TierRaceTest

race:
	@for i in $$(seq 1 10); do \
	  echo "=== race run $$i/10"; \
	  out=$$(uv run python manage.py test $(RACE_TESTS) -v 2 2>&1) \
	    || { echo "$$out"; echo "RUN $$i FAILED"; exit 1; }; \
	  echo "$$out" | grep -E '^Ran |^OK|^FAILED'; \
	  if echo "$$out" | grep -q 'skipped'; then \
	    echo "SKIPPED, so nothing was proven. DATABASE_URL must be Neon's DIRECT host"; \
	    echo "(no -pooler in the hostname), not SQLite."; \
	    exit 1; \
	  fi; \
	done; \
	echo ""; \
	echo "10/10 green: capacity never exceeded, and every tier sold exactly its seats."

# Mirrors render.yaml's startCommand so the worker model under test is the deployed one.
# Uses whatever DATABASE_URL .env points at -- run this against a scratch *Postgres*
# database, never the production Neon one: every booking it makes holds a real seat for
# the TTL. Not SQLite either: four threads writing one SQLite file produce 'database is
# locked' 500s that say nothing about the app (seen: 26 of 92 writes).
loadtest:
	uv run gunicorn config.wsgi:application --threads 4 --bind 127.0.0.1:8000 & \
	  GUNICORN_PID=$$!; sleep 2; \
	  uv run python scripts/loadtest.py --write; STATUS=$$?; \
	  kill $$GUNICORN_PID; exit $$STATUS

check-deploy:
	uv run python manage.py check --deploy

frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build

frontend-preview:
	cd frontend && npm run preview
