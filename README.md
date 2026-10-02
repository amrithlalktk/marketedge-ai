# MarketEdge AI

An explainable **analysis** platform for Indian equities (NSE swing setups) and NIFTY options (Phase 2). It is designed to extend to crypto, global stocks and forex.

For every setup it shows:
- entry, stop and targets, with the method used to derive each;
- a multi-factor score;
- the historical target-hit rate of the *same* rules, with sample size, backtest period and conditioning;
- a validation checklist.

If a setup doesn't pass every blocking check, the platform says **NO TRADE**.

> Historical/backtested performance and probability estimates do not guarantee future results. Market conditions can change rapidly. This platform provides analytical information and does not guarantee profits.

Architecture, schema, API, security and phases are covered in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start (Docker)

```bash
cp .env.example .env         # set POSTGRES_PASSWORD, SECRET_KEY, BOOTSTRAP_ADMIN_PASSWORD
docker compose up --build -d
make bootstrap-sample         # loads SAMPLE (synthetic) data and runs the first scan
open http://localhost:3000    # API docs: http://localhost:8000/docs
```

## Quick start (no Docker)

```bash
./scripts/dev-local.sh        # SQLite + SAMPLE data + eager jobs, API on :8000
cd frontend && npm install && npm run dev   # UI on :3000
```

Log in as `admin@example.com` with the password `Adm1n!Password`. This is a development credential only.

## Data

`MARKET_DATA_PROVIDER=sample` generates **synthetic** prices. Every symbol is prefixed `DEMO_`, every payload carries `is_sample: true`, and the UI shows a permanent banner. None of it is real market data.

For real analysis:
1. Place files you have licensed in the `csv` layout described in [csv_provider.py](backend/app/providers/csv_provider.py).
2. Set `MARKET_DATA_PROVIDER=csv` (and `OPTIONS_DATA_PROVIDER=csv`, `OPTIONS_UNDERLYING=<index symbol>` for options) and point `BENCHMARK_SYMBOL` / `VIX_SYMBOL` at your index symbols.

You can also implement a licensed vendor adapter in `backend/app/providers/` (see ARCHITECTURE §5).

## Everyday commands

| | |
|---|---|
| `python -m app.cli ingest [--full]` | pull bars from the configured provider |
| `python -m app.cli scan` | run the full analysis scan (normally scheduled weekdays 18:30 IST by Celery beat) |
| `python -m app.cli options` | ingest the option chain and run the NIFTY options analysis (also scheduled after the EOD scan) |
| `python -m app.cli create-admin EMAIL PASSWORD` | create or promote an admin |
| `cd backend && ./.venv/bin/python -m pytest -q` | run the test suite |
| `alembic upgrade head` / `alembic revision --autogenerate -m "..."` | migrations |

## Simple mode

A single-user install can switch off everything it does not use. Nothing is deleted, and each line can be undone:

```
MARKETS_ENABLED=NSE,CRYPTO                       # only markets with real data
FEATURES_DISABLED=news,calendar,analyst,ml,analytics,backtest,strategies
ALLOW_REGISTRATION=false                         # only your existing account
```

For a switched-off feature:

* its pages are hidden;
* its API routes are not served;
* its scheduled jobs are not run.

Remove a name to bring the feature back.

## Production

* [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) covers the single-host compose setup with automatic TLS, Kubernetes, and the AWS and Azure mappings.
* [docs/OPERATIONS.md](docs/OPERATIONS.md) covers monitoring, backups and restore, scaling, load testing and incidents.

Production refuses to start with sample data unless `ALLOW_SAMPLE_IN_PRODUCTION=true`.

## Environment variables

These are listed in [.env.example](.env.example). All backend settings are defined in [backend/app/core/config.py](backend/app/core/config.py).
