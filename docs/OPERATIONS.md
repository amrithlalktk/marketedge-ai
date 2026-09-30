# MarketEdge AI — Operations

This runbook is for whoever runs the platform: monitoring, backups and restore, scaling, load testing and common incidents. For installing it, see [DEPLOYMENT.md](DEPLOYMENT.md).

## Processes and queues

| Process | Queue(s) | Pool | What runs there |
|---|---|---|---|
| `api` | — | uvicorn × `WEB_CONCURRENCY` | HTTP API. `/health` is liveness, `/ready` is readiness, `/metrics` is Prometheus. |
| `worker` | `default`, `notifications` | prefork × 2 | news, calendars, retention cleanup, alert ticks (5 min), notification retries, Telegram poll |
| `worker-scans` | `scans` | **solo** | ingest, scan, ingest+scan, options. Each task forks `SCAN_WORKERS` per-symbol processes. |
| `worker-backtests` | `backtests` | **solo** | backtests and ML training (forks `SCAN_WORKERS`, set from `BACKTEST_WORKERS` in compose) |
| `beat` | — | — | the scheduler. **Run exactly one**: two would enqueue every job twice. |

The heavy workers use the solo pool because Celery prefork children are daemonic and cannot fork. With solo, one task runs at a time per worker, and that task parallelises across symbols. To add capacity, run more worker replicas. Per-symbol parallelism is Linux-only; on macOS and Windows the code runs serially and gives the same results.

The figures below are measured on the 8-CPU dev container, NSE sample universe.

**Feature and event build:**

| Setting | Features | Events | Total | Trades |
|---|---|---|---|---|
| `SCAN_WORKERS=1` | 6.0 s | 3.6 s | 9.6 s | 5,447 |
| `SCAN_WORKERS=4` | 1.8 s | 1.0 s | 2.9 s | 5,447 (identical) |

**Full jobs, run through the queues:**
* CRYPTO scan: 3.1 s.
* NSE single-strategy walk-forward backtest: 9.9 s.

## Monitoring

**Logs.** Set `LOG_FORMAT=json` and each log line becomes one JSON object with these fields:
* `ts`, `level`, `logger`, `message`, `request_id`;
* on access lines, also `method`, `route`, `status`, `duration_ms`, `client_ip`.

**Request IDs.** Every response carries `X-Request-ID`. A client-supplied ID that is 8–64 characters from `[A-Za-z0-9._-]` is kept; anything else is replaced. Quote this ID when you investigate a user report.

**Metrics.** `GET /metrics` requires `Authorization: Bearer $METRICS_TOKEN`. In production it returns 404 when no token is configured. The API Service is internal-only in both the Kubernetes and Caddy setups, and Caddy additionally returns 404 for `/metrics`, `/docs` and `/ready`.

| Metric | Meaning |
|---|---|
| `marketedge_http_requests_total{method,route,status}` | Request counter. `route` is the route template (for example `/api/v1/stocks/{symbol}/analysis`), so label cardinality stays bounded. |
| `marketedge_http_request_duration_seconds` | Latency histogram, per route. |
| `marketedge_jobs_24h{kind,status}` | Background jobs in the last 24 h. |
| `marketedge_scan_valid_setups{market}` | VALID setups in the latest successful scan. |
| `marketedge_scan_age_hours{market}` | Hours since the latest successful scan. |
| `marketedge_scan_last_failed{market}` | 1 if the most recent scan attempt failed. |
| `marketedge_data_lag_days{market}` | Worst lag in days between an instrument's last bar and the expected session. This is the same number that drives the ⚠ DATA DELAYED banner. |
| `marketedge_notification_deliveries_24h{channel,status}` | Notification delivery outcomes. |
| `marketedge_ml_active_models{market}` | Active ML models. |

The job, scan, data and notification gauges are computed from Postgres at scrape time. Celery workers therefore need no exporter, and the numbers agree with what the UI shows. HTTP metrics use prometheus_client multiprocess mode, so all uvicorn workers are counted; the start script resets the directory on boot.

**Alert rules.** `ops/prometheus/alerts.yml` defines these alerts:
* API down;
* 5xx rate above 2%;
* p95 above 1.5 s;
* a scan failed;
* the crypto scan is stale;
* data lag above 3 days;
* 3 or more failed jobs of one kind;
* notification failures.

**Dashboard.** Import `ops/grafana/marketedge-dashboard.json` into Grafana.

**Errors.** Set `SENTRY_DSN` to enable Sentry in the API and workers. `send_default_pii` is off, and tracing is off unless you set `SENTRY_TRACES_SAMPLE_RATE`.

## Readiness vs liveness

* **`/health`** checks only that the process is up. Do not point liveness probes at dependencies: a database blip would then restart every pod.
* **`/ready`** returns 503 when any of these fails, which takes the pod out of the load balancer:
  * the database is unreachable;
  * migrations are pending (the database is behind the code);
  * Redis is unreachable.

## Backups and restore

**What is backed up.**
* **Kubernetes:** the `db-backup` CronJob runs at 21:30 UTC (03:00 IST) and writes a `pg_dump` custom-format archive. It verifies the archive with `pg_restore --list` before keeping it, and keeps 14 days.
* **Compose:** `docker-compose.prod.yml` runs the same script (`ops/backup/backup.sh`) in a `backup` service.
* **Off-site copy:** set `BACKUP_UPLOAD_CMD` to ship each archive off the host. Examples:
  * `aws s3 cp "$1" s3://…/`
  * `az storage blob upload -f "$1" …`

  Enable versioning and a lifecycle rule on the bucket.
* **Managed Postgres:** also enable its point-in-time recovery (RDS / Azure Flexible Server). A logical dump is your cross-provider, human-readable copy; PITR is your fast path.

**Restore into a new database** (rehearse this quarterly):

```sh
createdb -h HOST -U marketedge marketedge_restore
pg_restore -h HOST -U marketedge -d marketedge_restore --no-owner --jobs=4 marketedge-YYYYMMDDTHHMMSSZ.dump
# point DATABASE_URL at marketedge_restore on one API pod, then check:
curl -s https://…/ready            # migrations "ok" means the schema matches this image
# then swap: rename databases in a maintenance window, or repoint DATABASE_URL everywhere
```

**Regenerable data.** Market data, scans and ML candidates can be rebuilt from providers:
* `python -m app.cli ingest --full`
* then `scan`
* then retrain

Users, strategies, strategy versions, portfolios, journal entries, alerts, audit logs and encrypted provider keys **cannot** be rebuilt, so they are why backups matter.

**Keep `ENCRYPTION_KEY` with the backups** (separately and securely). Without it, the TOTP secrets and stored provider keys in a restored database cannot be decrypted.

## Retention

The daily `retention_cleanup` task (03:45 IST, default queue) deletes:

| Data | Retention | Setting |
|---|---|---|
| Audit logs | 365 days | `AUDIT_RETENTION_DAYS` |
| Notifications | 90 days | `NOTIFICATION_RETENTION_DAYS` |
| Finished job records | 90 days | fixed |

Running jobs are never deleted.

## Database

**Hot-path indexes (migration 0008):**
* latest scan per market;
* the job window;
* unread notifications;
* a scan's signals by score;
* open paper trades.

On a very large live database, create them with `CREATE INDEX CONCURRENTLY` by hand before running the migration, then `alembic stamp 0008`.

**Partitioning.** `ops/partition_market_data.sql` range-partitions `market_data` by year. It is only worth doing past about 50M bars (intraday data or very large universes). Run it on a restored copy first.

**Connection budget.** Each process holds up to `DB_POOL_SIZE + DB_MAX_OVERFLOW` connections. Size Postgres `max_connections`, or put PgBouncer in front, for:

> (api replicas × WEB_CONCURRENCY + worker processes) × pool

The Kubernetes config uses 5 + 5 per process.

## Caching

**Redis** holds the analysis page payload, candles, the events frame, rate-limit windows, and short-lived API caches. Frames are stored as JSON; pickle is never used. After each scan the `me:` cache prefix is cleared.

**Analysis payload:**
* Keyed per scan run and calendar day, with a 3 h TTL.
* After each scan, the top `PREWARM_TOP_N` symbols (VALID setups first) are pre-computed.

**Candles:**
* Keyed on the instrument's last bar and fetch time, so a new or revised bar is never served stale.
* Data freshness (`data`) is computed on every request and is never cached.

**Events frame.** It is also memoised in-process per events backtest id. That id's content is immutable.

## Load testing

`scripts/loadtest.py` logs in once. It then runs N virtual users against the dashboard, scanner, stock-detail and options read paths, and reports p50/p95/p99 for each endpoint. It exits non-zero if the p95 or error-rate limit is exceeded.

The rate limiter is per client IP (120/min by default). To test without touching the running stack, start a throw-away API with a high limit:

```sh
docker compose run -d --no-deps --name me-loadtest -p 8001:8000 \
  -e RATE_LIMIT_PER_MINUTE=10000000 -e RUN_MIGRATIONS=false api
backend/.venv/bin/python scripts/loadtest.py --base http://localhost:8001 \
  --email "$ADMIN_EMAIL" --password "$ADMIN_PASSWORD" --users 20 --duration 60
docker rm -f me-loadtest
```

Measured results (one API container, 2 uvicorn workers, sample data, 20 users with 50–250 ms think time) are recorded in [ARCHITECTURE.md §13](ARCHITECTURE.md#13-estimated-infrastructure).

**How to read the results.** Page requests that hit the cache take tens of milliseconds. A cold stock page (`/analysis`, about 0.6 s CPU) is the expensive request. Because it is CPU-bound, add capacity by adding API processes (`WEB_CONCURRENCY` or replicas; the HPA targets 70% CPU), not threads.

## Common incidents

| Symptom | Check | Fix |
|---|---|---|
| ⚠ DATA DELAYED / `marketedge_data_lag_days` high | Admin → Data status; provider errors in `data_status.error`; `worker-scans` logs | Provider outage or expired key: fix the key in Admin → Providers, then Admin → Jobs → Ingest. Setups stay NO TRADE while data is stale, by design. |
| Scan did not run | `marketedge_scan_age_hours`; `beat` logs; is `worker-scans` consuming `scans`? | Restart `beat` (only one). Enqueue from Admin → Jobs. |
| Scans slow | `SCAN_WORKERS` vs CPU; is the worker using `--pool=solo`? (prefork runs serially) | Set the solo pool; match `SCAN_WORKERS` to the CPU request. |
| Alerts or notifications late | `worker` logs; `marketedge_notification_deliveries_24h{status="failed"}` | Channel credentials in Admin → Providers. Retries run every 15 min. |
| `/ready` 503 "migrations pending" | `alembic current` vs head | Run the migrate Job / `alembic upgrade head`. |
| 429s for many users behind one proxy | Is `TRUSTED_PROXIES` missing the proxy? | Add the proxy CIDR so the real client IP is used. |
