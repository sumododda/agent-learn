.PHONY: dev-db dev-backend dev-frontend dev-worker dev migrate install cert-check

dev-db:
	docker compose up -d db

dev-backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

dev-worker:
	cd backend && uv run python -m app.worker

migrate:
	cd backend && uv run alembic upgrade head

install:
	cd backend && uv pip install -r requirements.txt
	cd frontend && npm install

cert-check:
	./deploy/cert-check.sh

dev:
	$(MAKE) dev-db
	@sleep 2
	$(MAKE) migrate
	cd backend && uv run uvicorn app.main:app --reload --port 8000 & \
	cd backend && uv run python -m app.worker & \
	cd frontend && npm run dev & \
	wait
