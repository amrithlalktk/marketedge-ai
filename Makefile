.PHONY: up down logs bootstrap-sample daily test migrate lint audit

up:            ## build and start the local stack (postgres + api + frontend)
	docker compose up --build -d
down:
	docker compose down
logs:
	docker compose logs -f api
bootstrap-sample: ## load SAMPLE (synthetic) data and run the first scans
	docker compose exec api python -m app.cli bootstrap-sample
daily:         ## make daily m=NSE  — run the end-of-day pipeline locally
	docker compose exec api python -m app.cli daily $(or $(m),NSE)
migrate:
	docker compose exec api alembic upgrade head
test:
	cd backend && ./.venv/bin/python -m pytest -q
lint:
	cd backend && ./.venv/bin/ruff check app engine tests
audit:
	cd backend && ./.venv/bin/bandit -q -r app engine
	cd frontend && npm audit --audit-level=high
