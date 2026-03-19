.PHONY: dev-db dev-backend dev-frontend dev migrate

dev-db:
	docker compose up -d db

dev-backend:
	cd backend && uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

migrate:
	cd backend && alembic upgrade head

dev:
	$(MAKE) dev-db
	@sleep 2
	$(MAKE) migrate
	cd backend && uvicorn app.main:app --reload --port 8000 & \
	cd frontend && npm run dev & \
	wait
