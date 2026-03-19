.PHONY: dev-db dev-backend dev-frontend dev-trigger dev migrate

dev-db:
	docker compose up -d db

dev-backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

dev-trigger:
	cd trigger && npx trigger.dev@latest dev

migrate:
	cd backend && uv run alembic upgrade head

dev:
	$(MAKE) dev-db
	@sleep 2
	$(MAKE) migrate
	cd backend && uv run uvicorn app.main:app --reload --port 8000 & \
	cd frontend && npm run dev & \
	cd trigger && npx trigger.dev@latest dev & \
	wait
