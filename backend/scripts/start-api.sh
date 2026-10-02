#!/bin/sh
# Local/Docker API entrypoint. RUN_MIGRATIONS=false to skip migrations.
set -eu
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  alembic upgrade head
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "${WEB_CONCURRENCY:-2}" --proxy-headers --no-server-header
