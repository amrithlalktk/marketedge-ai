# MarketEdge AI (lite)

An explainable **analysis** app for **NSE stocks**, **NIFTY options** and **crypto**.

Every trading day it scans the market. For each setup that passes every safety check, it shows:
- the stock or option contract;
- the entry, stop loss and exits;
- the **historical chance** that the same rules reached their target before the stop, with the number of past cases.

If nothing passes, it says **NO TRADE**.

> Historical/backtested performance and probability estimates do not guarantee future results. This app provides analytical information and does not guarantee profits.

## What's in it

**Pages:**
- Dashboard with **Today's trade ideas**;
- Setups, with the NO TRADE list and its reasons;
- Stocks, with charts and analysis;
- Watchlists;
- paper-trading Portfolio;
- Alerts, including the daily ideas message;
- NIFTY Options;
- Risk calculator;
- Account (2FA);
- Admin: jobs, strategy on/off switches, providers and users.

**Data:**
- **Upstox** for NSE stocks and the NIFTY option chain, using your personal analytics token.
- **Binance** public data for crypto.
- `sample` providers generate clearly labelled synthetic `DEMO_` data for development.

## Run it

- **Production (free):** Vercel (website and API) + Neon (Postgres) + GitHub Actions (the daily scan). See **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.
- **Local:**

  ```bash
  cp .env.example .env           # set POSTGRES_PASSWORD, SECRET_KEY, BOOTSTRAP_ADMIN_PASSWORD
  docker compose up --build -d
  make bootstrap-sample          # SAMPLE (synthetic) data + first scans
  open http://localhost:3000     # admin@example.com / Adm1n!Password (development only)
  ```

## Commands

| | |
|---|---|
| `python -m app.cli daily NSE\|CRYPTO [--full]` | the end-of-day pipeline (what the schedule runs) |
| `python -m app.cli ingest\|scan [--market NSE]`, `options` | single steps |
| `python -m app.cli create-admin EMAIL PASSWORD` | create or promote an admin |
| `make test` / `make lint` | backend tests / lint |

Architecture notes are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
