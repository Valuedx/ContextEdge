.PHONY: up down dev logs migrate seed test lint format

# Infrastructure only (PostgreSQL, Redis, MinIO)
up:
	docker compose up -d

down:
	docker compose down

# Full development stack
dev:
	docker compose -f docker-compose.dev.yml up --build

dev-down:
	docker compose -f docker-compose.dev.yml down

logs:
	docker compose -f docker-compose.dev.yml logs -f

# Backend commands (run from host with venv active)
migrate:
	cd backend && alembic upgrade head

migrate-new:
	cd backend && alembic revision --autogenerate -m "$(msg)"

migrate-down:
	cd backend && alembic downgrade -1

seed:
	cd backend && python dev.py seed

backend-dev:
	cd backend && python dev.py api

celery-dev:
	cd backend && python dev.py worker

celery-beat-dev:
	cd backend && python dev.py beat

# Frontend commands
frontend-dev:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build

# Testing
test:
	cd backend && python -m pytest
	cd frontend && npm test

test-backend:
	cd backend && python -m pytest -v

test-frontend:
	cd frontend && npm test

# Code quality
lint:
	cd backend && ruff check src/
	cd frontend && npm run lint

format:
	cd backend && ruff format src/
	cd frontend && npm run format
