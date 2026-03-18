.PHONY: dev-db dev-backend dev-frontend dev

dev-db:
	docker compose up -d db

dev-backend:
	cd backend && uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

dev:
	$(MAKE) dev-db
	@echo "Run 'make dev-backend' and 'make dev-frontend' in separate terminals"
