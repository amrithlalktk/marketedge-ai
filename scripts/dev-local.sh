#!/usr/bin/env bash
# Run the API locally without Docker, on SQLite + in-process cache + eager jobs.
# Uses SAMPLE (synthetic) data. For development only.
set -euo pipefail
cd "$(dirname "$0")/../backend"
[ -d .venv ] || { python3 -m venv .venv && ./.venv/bin/pip install -q -r requirements-dev.txt; }
export ENVIRONMENT=development DATABASE_URL="sqlite:///./dev.db" REDIS_URL="" JOB_RUNNER=inline \
       MARKET_DATA_PROVIDER=sample SECRET_KEY="dev-only-secret-key-please-change-0123456789"
if [ ! -f dev.db ]; then
  ./.venv/bin/python -c "from app.core.db import Base, engine; import app.models; Base.metadata.create_all(engine)"
  ./.venv/bin/python -m app.cli create-admin admin@example.com 'Adm1n!Password'
  ./.venv/bin/python -m app.cli bootstrap-sample
fi
exec ./.venv/bin/uvicorn app.main:app --reload --port 8000
