.PHONY: up down logs bootstrap-sample test migrate revision lint audit loadtest k8s-render prod-up

up:            ## build and start the stack
	docker compose up --build -d
down:
	docker compose down
logs:
	docker compose logs -f api worker worker-scans worker-backtests beat
bootstrap-sample: ## load SAMPLE (synthetic) data and run the first scan
	docker compose exec api python -m app.cli bootstrap-sample
migrate:
	docker compose exec api alembic upgrade head
revision:      ## make revision m="message"
	docker compose exec api alembic revision --autogenerate -m "$(m)"
test:
	cd backend && ./.venv/bin/python -m pytest -q
lint:
	cd backend && ./.venv/bin/ruff check app engine tests
audit:         ## security scans (backend deps are audited best on the 3.11 image: see CI)
	cd backend && ./.venv/bin/bandit -q -r app engine && ./.venv/bin/pip-audit -r requirements.txt
	cd frontend && npm audit --audit-level=high
loadtest:      ## make loadtest base=http://localhost:8001 email=... password=...
	backend/.venv/bin/python scripts/loadtest.py --base "$(or $(base),http://localhost:8000)" --email "$(email)" --password "$(password)"
k8s-render:
	kubectl kustomize deploy/k8s/overlays/production
prod-up:       ## single-host production (see docs/DEPLOYMENT.md)
	docker compose -f docker-compose.prod.yml up -d --build
