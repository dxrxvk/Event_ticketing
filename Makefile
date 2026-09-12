.DEFAULT_GOAL := help

.PHONY: help install migrate makemigrations superuser shell run test check-deploy \
        frontend-install frontend-dev frontend-build frontend-preview

help:
	@echo "Backend (Django, via uv):"
	@echo "  make install         uv sync"
	@echo "  make run             runserver"
	@echo "  make migrate         apply migrations"
	@echo "  make makemigrations  generate migrations"
	@echo "  make superuser       createsuperuser"
	@echo "  make shell           Django shell"
	@echo "  make test            run tickets test suite"
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
