.PHONY: dev-db dev-backend dev-frontend dev migrate install

dev-db:
	docker compose up -d db

dev-backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

migrate:
	cd backend && uv run alembic upgrade head

install:
	cd backend && uv pip install -r requirements.txt
	cd frontend && npm install

dev:
	$(MAKE) dev-db
	@sleep 2
	$(MAKE) migrate
	cd backend && uv run uvicorn app.main:app --reload --port 8000 & \
	cd frontend && npm run dev & \
	wait
