# MarketEdge AI — Architecture

> **Lite build (October 2026).** The deployed app was reduced to **NSE + NIFTY options + crypto** and runs on **Vercel (website and API) + Neon Postgres + GitHub Actions (daily pipeline)**. See [DEPLOYMENT.md](DEPLOYMENT.md).
>
> **Removed, still in git history:**
> - US, Europe, Asia and forex markets;
> - news and sentiment, calendars;
> - the AI analyst and the ML layer;
> - the hit-rate explorer, the backtest lab and the custom strategy builder;
> - Celery, Redis and the Kubernetes and Docker production manifests.
>
> Sections below that describe these components are historical.


MarketEdge AI is an **analysis and research platform**. It finds trading setups using explicit rules, then reports how the *same* rules performed historically (sample size, period, methodology). It does not predict the future and never presents probabilities as guarantees.

Status markers in this document: **[built]** = implemented and tested in this repository; **[phase N]** = designed here, delivered in that phase.

---

## 1. Complete architecture

```
                ┌────────────────────────────── Browser (mobile-first Next.js) ─────────────────────────────┐
                │  Dashboard · Top Setups · Stock detail + charts · Backtest/Strategy builder · Watchlists │
                │  Risk calculator · Admin          access token in memory, refresh token = httpOnly cookie  │
                └──────────────────────────────────────────┬──────────────────────────────────────────────────┘
                                                           │ same-origin /api/v1/* (Next.js rewrite → API)
┌──────────────────────────────────────────────────────────▼───────────────────────────────────────────────────┐
│ FastAPI  (app/)                                                                                               │
│  middleware: security headers · rate limit · CORS      auth: JWT access + rotating refresh · TOTP 2FA · RBAC  │
│  routes: auth markets stocks signals strategies backtests watchlists risk admin                               │
│  READS precomputed results; only cheap single-symbol evaluation happens in-request                          │
└──────────────┬───────────────────────────────┬──────────────────────────────────────┬────────────────────────┘
               │ SQLAlchemy                    │ cache (Redis, JSON only)             │ enqueue
        ┌──────▼──────┐                 ┌──────▼──────┐                      ┌────────▼─────────┐
        │ PostgreSQL  │◄────────────────┤    Redis    │◄─────────────────────┤  Celery workers  │
        │ OHLCV, setups│                │ cache+broker│                      │ ingest · scan ·  │
        │ backtests,…  │                └─────────────┘                      │ backtest · beat  │
        └──────▲──────┘                                                       └───┬─────────┬────┘
               │ upsert                                                           │         │ uses
      ┌────────┴──────────────── providers/ (abstraction) ──────────────┐         │   ┌─────▼────────────────────┐
      │ MarketDataProvider · FundamentalDataProvider · OptionsDataProvider│◄────────┘   │ engine/  (pure Python)   │
      │ CryptoDataProvider · NewsProvider · EconomicCalendarProvider      │             │ indicators → structure → │
      │ adapters: sample (SYNTHETIC) · csv (licensed files) · vendor APIs │             │ strategies → levels →    │
      └───────────────────────────────────────────────────────────────────┘             │ backtest → probability → │
                                                                                        │ regime/breadth → scoring │
                                                                                        │ → validation (NO TRADE)  │
                                                                                        └──────────────────────────┘
```

Key decisions:

| Decision | Why |
|---|---|
| **The analysis engine is a separate package (`backend/engine`)** with no web, DB or provider imports. | The engine is the product. It can be versioned, unit-tested, run in notebooks, or split into its own service later without changes. |
| **The engine reads only from the database, never straight from providers.** | Every result can be reproduced from stored bars. Each scan stores the engine version and a config snapshot. |
| **Expensive work runs only in workers.** Web requests read precomputed scans; single-symbol analysis reuses stored historical events. | Keeps latency predictable. A full-universe scan can take minutes. |
| **Live setups and backtests use one rule set.** The detector, level engine and trade simulator are identical in both. | The stated hit rate describes exactly the rule the user is shown. |
| **Instruments are unified in one table** (`asset_class` = EQUITY/INDEX/ETF/CRYPTO/FOREX/…). | Crypto, global and forex (Phase 4) reuse the OHLCV table, scanner and backtester unchanged. This replaces the separate `stocks` / `crypto_assets` / `forex_pairs` tables suggested in the brief. |

## 2. Folder structure

```
.
├── docker-compose.yml, docker-compose.prod.yml (Caddy TLS), .env.example, Makefile
├── docs/ARCHITECTURE.md                 ← this file; DEPLOYMENT.md, OPERATIONS.md (Phase 8)
├── scripts/dev-local.sh                 ← run API on SQLite + SAMPLE data, no Docker
├── scripts/loadtest.py                  ← read-path load test (p50/p95/p99 per endpoint)
├── deploy/k8s/{base,overlays/production} ← kustomize manifests
├── ops/                                 ← prometheus config + alerts, grafana dashboard, caddy, backup.sh, partition SQL
├── .github/workflows/ci.yml             ← lint, tests, migrations, audits, image build + Trivy
├── backend/
│   ├── engine/                          ← MARKET ANALYSIS ENGINE (pure pandas/numpy)
│   │   ├── config.py        thresholds, weights, labels, costs (all admin-overridable)
│   │   ├── indicators.py    EMA/SMA/RSI/MACD/ADX/ATR/BB/Stoch/CCI/ROC/OBV/MFI/VWAP/Supertrend
│   │   ├── structure.py     confirmed pivots, swing HH/HL/LH/LL, support/resistance clustering
│   │   ├── features.py      feature pipeline + weekly (completed-week) trend + pattern flags
│   │   ├── strategies.py    built-in setups + eval-free rule DSL for the strategy builder
│   │   ├── levels.py        entry zone / stop / T1-T3 with methodology strings
│   │   ├── backtest.py      trade simulator (gap, same-bar, chase, partial exits, costs)
│   │   ├── probability.py   target-hit engine (point-in-time, hierarchical similarity)
│   │   ├── metrics.py       hit rates, Wilson CI, PF, CAGR, DD, Sharpe, Sortino, Monte Carlo
│   │   ├── walkforward.py   train/val/OOS, walk-forward, sensitivity, overfit warnings
│   │   ├── regime.py        market regime classification
│   │   ├── breadth.py       breadth + sector rotation
│   │   ├── mtf.py           multi-timeframe alignment
│   │   ├── scoring.py       multi-factor score (components kept visible)
│   │   ├── validation.py    NO TRADE engine (12-point checklist)
│   │   ├── explain.py       "why this setup" / risks / invalidation — from computed values only
│   │   ├── risk.py          position sizing, ATR sizing, portfolio risk
│   │   └── analyzer.py      orchestrator
│   ├── app/
│   │   ├── core/            config, db, security (JWT/argon2/TOTP/Fernet), rbac, cache, middleware
│   │   ├── models/          SQLAlchemy models
│   │   ├── schemas/         Pydantic request validation
│   │   ├── providers/       base interfaces, sample (synthetic), csv, registry
│   │   ├── services/        market_data (ingest/load), scan_service, backtest_service, settings, audit
│   │   ├── workers/         Celery app + tasks + beat schedule
│   │   ├── api/routes/      REST endpoints
│   │   ├── cli.py           create-admin · ingest · scan · bootstrap-sample
│   │   └── main.py
│   ├── alembic/             migrations (0001 initial schema)
│   └── tests/               engine correctness + API integration
└── frontend/                Next.js 15 + TypeScript + Tailwind + lightweight-charts
```

## 3. Database schema (PostgreSQL) **[built]**

The migration is `backend/alembic/versions/20260929_0001_initial_schema.py`.

| Table | Purpose / key columns |
|---|---|
| `users` | email (unique), argon2 `password_hash`, `role_id`, `totp_secret_enc` (Fernet), `totp_enabled`, `failed_logins`, `locked_until` |
| `roles`, `permissions`, `role_permissions` | RBAC. Seeded idempotently: standard, premium, analyst, admin |
| `user_sessions` | Refresh-token **hashes**, `family` (rotation lineage), `expires_at`, `revoked_at` |
| `audit_logs` | action, target, detail JSON, ip, timestamp |
| `api_keys` | provider credentials, **encrypted**, never returned by the API |
| `app_settings` | `engine_config` overrides (weights, labels, thresholds) |
| `sectors`, `instruments` | unified instrument master; `listed_on`/`delisted_on` for point-in-time universes; `is_sample` |
| `market_data` | OHLCV. PK (`instrument_id`, `interval`, `ts`) doubles as the time-range index. Partition by month, or move to TimescaleDB, past ~50M rows |
| `data_status` | per instrument+interval: `source`, `last_bar_ts`, `fetched_at`, `error`, `is_sample` → drives ⚠ DATA DELAYED |
| `fundamentals` | JSON by (`instrument_id`, `as_of`, `source`) |
| `strategies`, `strategy_versions` | custom strategies; every edit creates an immutable version (migration `0003`), `include_in_scan` puts a strategy into the daily scan; built-ins live in code |
| `scan_runs` | each scan: engine version, **config snapshot**, stats, `events_backtest_id` |
| `signals` | every evaluated setup (VALID and NO_TRADE) with the full explainable payload; index (`status`, `as_of`, `score`) |
| `signal_history` | forward-tracked outcome of each published VALID setup: a live track record, separate from the backtest |
| `backtests`, `backtest_trades` | user backtests (pinned to a `strategy_version_id`; trades carry `t1_date`), plus the per-scan historical event set (`kind=system_events`) that feeds the probability engine |
| `portfolios`, `portfolio_trades`, `alerts`, `notifications`, `notification_settings` (migration `0007`) | paper/journal portfolios with engine state per trade; alerts; notifications with per-channel delivery; channel settings |
| `ml_models` (migration `0006`) | versioned models: status, gate, walk-forward metrics, reliability, importance, JSON/skops artifact |
| `news`, `news_symbols`, `earnings_events`, `economic_events`, `analyst_queries` (migration `0005`) | articles with sentiment and matched terms; calendars; AI analyst audit (answer, grounding, token usage) |
| `instruments.market` (migration `0004`), `crypto_derivatives` | market partition key; daily funding / OI / long-short context per coin |
| `market_regimes`, `market_snapshots` | regime per day; overview / breadth / sectors / strategy-performance snapshots |
| `watchlists`, `watchlist_items` | per-user lists; tags JSON, notes |
| `jobs` | background job lifecycle for the admin dashboard |

Phase 2 tables **[built]** (migration `0002`):

| Table | Purpose |
|---|---|
| `options_contracts` | (underlying, expiry, strike, CE/PE, lot size) |
| `options_chain` | per-contract quote per snapshot: bid/ask/LTP/volume/OI/ΔOI, vendor IV when supplied |
| `options_iv_history` | daily near-expiry ATM IV, the basis for IV percentile |

Option setups are stored in `signals` with `market = 'NFO'` under their own `scan_runs` row, so they never mix with equity setups. The full options view is a `market_snapshots` row (`kind = 'options'`).

Tables added in later phases: `portfolios`, `portfolio_positions` and `paper_trades` (P7); `alerts` and `notifications` (P7); `news` (P5).

## 4. API architecture **[built]**

REST under `/api/v1`, with OpenAPI at `/docs` (disabled in production). All endpoints except register/login/refresh/health require a Bearer token plus a permission.

| Method & path | Permission | Notes |
|---|---|---|
| `POST /auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout` | – | login → access JWT (15 min) + httpOnly refresh cookie (rotated; reuse revokes the family). Returns 423 after 5 failures |
| `GET /auth/me`; `POST /auth/2fa/setup|enable|disable` | auth | TOTP (RFC 6238) |
| `GET /markets/overview`, `/markets/regime`, `/markets/breadth`, `/markets/sectors` | market:read | read from snapshots |
| `GET /stocks`, `/stocks/{symbol}`, `/stocks/{symbol}/candles`, `/stocks/{symbol}/analysis?mode=` | analysis:read | analysis evaluates every strategy on the latest bar |
| `GET /signals/top?sort=&direction=` | signals:read | standard: max 5 results; includes `sort_note` and disclaimer |
| `GET /signals?status=VALID|NO_TRADE`, `GET /signals/{id}`, `GET /signals/track-record` | signals:read_all / signals:read | |
| `GET /strategies`, `/strategies/{key}`, `/strategies/{key}/versions/{n}`, `/strategies/{key}/performance`, `POST /strategies/validate` | analysis:read | performance panel; DSL validation |
| `POST /strategies`, `PUT /strategies/{key}` (new version), `PATCH /strategies/{key}` (scan inclusion), `DELETE` | strategies:manage | audit-logged |
| `GET /backtests/compare/summary?ids=`, `GET /backtests/{id}/trades.csv` | backtests:run | compare 2–5 backtests; CSV export (formula-injection safe) |
| `GET /analytics/hit-rates` | signals:read_all | hit-rate explorer |
| `POST /backtests` (202), `GET /backtests`, `/backtests/{id}`, `/backtests/{id}/trades` | backtests:run | runs in a worker; owner-only |
| `GET/POST/DELETE /watchlists`, `POST/PATCH/DELETE /watchlists/{id}/items[/{item}]` | watchlists:write | standard: 3 lists |
| `POST /risk/position-size`, `/risk/portfolio` | auth | |
| `GET /admin/health|providers|jobs|users|audit-logs`, `GET/PUT /admin/settings/engine`, `POST /admin/jobs/ingest|scan`, `POST /admin/providers/keys`, `PATCH /admin/users/{id}` | admin:* | every mutation is audit-logged |

**Options [built]:**

| Method & path | Permission | Notes |
|---|---|---|
| `GET /options/nifty` | options:read (standard+) | market state, intraday, IV, expected move, OI analytics, futures basis |
| `GET /options/nifty/expiries`, `GET /options/nifty/chain?expiry=&width=` | options:read | chain with Greeks and liquidity flags |
| `POST /options/payoff` | options:read | custom multi-leg payoff builder |
| `GET /options/signals`, `GET /options/strategies` | options:signals (premium+) | option setups; strategy engine |
| `POST /admin/jobs/options` | admin:jobs | ingest the chain, then analyse |

**Multi-market [built]:**

| Method & path | Notes |
|---|---|
| `GET /markets`, `GET /markets/global-overview` | markets list; cross-market dashboard |
| `GET /markets/{overview,regime,breadth,sectors}?market=`, `GET /signals/top?market=`, `/signals?market=`, `/analytics/hit-rates?market=`, `/stocks?market=` | per-market views |
| `GET /crypto/signals`, `/global/signals?region=US\|EUROPE\|ASIA`, `/forex/signals` | per-market setup lists |
| `POST /admin/jobs/{ingest,scan}?market=` | admin jobs per market |

**Events & AI [built]:**

| Method & path | Permission | Notes |
|---|---|---|
| `GET /news`, `/news/{id}` | market:read | filters: symbol, market, sentiment, category, days |
| `GET /calendar/economic`, `/calendar/earnings` | market:read | |
| `GET /stocks/{symbol}/events` | analysis:read | |
| `POST /analyst/ask`, `GET /analyst/history` | analyst:ask (premium+) | |
| `POST /admin/jobs/news?market=`, `/admin/jobs/calendar` | admin:jobs | |

**ML [built]:**

| Method & path | Permission | Notes |
|---|---|---|
| `GET /ml/models`, `/ml/models/{id}` | signals:read_all | |
| `PATCH /ml/models/{id}` | admin:settings | activation requires the gate |
| `GET /ml/regime?market=` | market:read | |
| `POST /admin/jobs/ml-train?market=` | admin:jobs | |

**Portfolio, alerts, notifications [built]:**

| Method & path | Permission | Notes |
|---|---|---|
| `/portfolios` (CRUD), `/portfolios/{id}/orders`, `/orders/from-setup/{signal_id}`, `/journal`, `/trades/{tid}` (PATCH), `/trades/{tid}/close` | portfolio:write | |
| `/alerts` (CRUD), `/alerts/kinds`, `/alerts/from-setup/{signal_id}` | alerts:write | |
| `/notifications`, `/notifications/read`, `/notifications/settings`, `/notifications/test`, `/notifications/telegram/link`, `/notifications/push/subscribe` | auth / alerts:write | |
| `/notifications/telegram/webhook` | secret header | |
| `POST /admin/jobs/paper-tick` | admin:jobs | advance paper trades and alerts now (normally every 5 minutes) |

Future endpoints follow the same pattern: `/crypto/signals`, `/global/signals`, `/forex/signals` (P4); `/portfolio`, `/paper-trades`, `/alerts` (P7).

## 5. Data-provider architecture

`app/providers/base.py` defines the interfaces `MarketDataProvider`, `FundamentalDataProvider`, `OptionsDataProvider`, `CryptoDataProvider`, `NewsProvider` and `EconomicCalendarProvider`. Every fetch returns `DataMeta` (`source`, `is_sample`, `as_of`, `fetched_at`, `delayed`, `error`). `registry.py` maps a config name to an adapter.

| Adapter | Status | Notes |
|---|---|---|
| `sample` | **[built]** | Deterministic regime-switching **synthetic** data. It is **prefix-stable**: fixed start dates and fixed-length random streams (`providers/_synth.py`), so each new day only appends a bar and incremental ingestion, paper fills and alerts behave as they would on real data. Symbols are prefixed `DEMO_`, everything is flagged `is_sample`, and the UI shows a permanent banner. It never fabricates fundamentals or earnings dates. |
| `csv` | **[built]** | Loads licensed vendor/broker exports (`instruments.csv`, `ohlcv/*.csv`, optional `fundamentals.csv`, `earnings.csv`). |
| NSE/BSE vendor or broker API | next | Implement `MarketDataProvider.get_ohlcv`. Candidates: authorised NSE data vendors (e.g. TrueData, Global Datafeeds) or broker APIs with historical data (Zerodha Kite Connect, Upstox, Angel One SmartAPI). The licence must permit analytical use and redistribution to your users. |
| `OptionsDataProvider`: `sample` / `csv` | **[built]** | The sample adapter produces a synthetic smile-shaped chain around the DEMO index, plus synthetic 5-minute index bars (labelled SAMPLE). The csv adapter reads licensed EOD chain snapshots (`options/{UNDERLYING}/{date}.csv` + `meta.csv`). |
| Live NSE F&O chain | next | Broker API or NSE-authorised vendor. Also needs the contract master (lot sizes, expiry calendar) and traded futures prices; the forward is currently theoretical. |
| Crypto | phase 4 | Exchange public market-data APIs (e.g. Binance, Coinbase, Kraken), plus derivatives data (funding, OI, liquidations) from exchange APIs or a licensed aggregator. |
| Global equities / FX | phase 4 | Licensed vendors (e.g. Polygon.io, Twelve Data, Tiingo, Alpha Vantage, EOD Historical Data). Terms vary by exchange. |
| News / calendars / fundamentals | phase 5 | Licensed news and fundamentals APIs. The AI analyst only ever sees backend-supplied data. |

The platform never scrapes websites. Ingestion upserts with a 7-day overlap so vendor revisions overwrite old bars. Caching uses Redis (JSON only, never pickle) with an in-process fallback.

## 6. Signal-generation architecture **[built]**

```
bars(DB) → features (indicators, confirmed pivots, completed-week trend, pattern flags)
        → strategy detectors (bar-t rules)  ──► levels(t): entry zone, stop, T1/T2/T3 + methodology
        → score(t): 9 visible components, configurable weights, fundamentals redistributed if missing
        → probability(t): outcomes of the same rules on history, exit_date < as_of only
        → MTF + regime context
        → validation: 17 checks (block/warn)  ──►  VALID | NO_TRADE (with reasons)
        → explanation: agreeing/disagreeing indicators, risks, invalidation, historical basis
```

* **Built-in setups:** Breakout + Volume; Trend Pullback to 20 EMA; Volatility Squeeze Breakout; Support Bounce (higher low); Breakdown + Volume (short); Downtrend Pullback (short). Short setups state that Indian cash equities cannot be shorted overnight.
* **Entry:** the next session's open. The trade is skipped if the open gaps more than 0.5×ATR beyond the signal close, or through the stop.
* **Stop:** the last confirmed swing low/high ±0.2 ATR when it lies 0.8–3.5 ATR away; otherwise 2×ATR.
* **Targets:** T1 = first S/R level between 1R and 2R (fallback 1.5R); T2 = next level beyond T1 (fallback 3R); T3 = 4.5R. Each target carries its method string.
* **Patterns detected [built]:** breakout, breakdown, consolidation, gap up/down, gap fill, double bottom/top, bull/bear flag, support bounce, resistance rejection, trend continuation, and reversal up/down. HH/HL/LH/LL come from the structure module.
* **Geometric patterns [built, Phase 6]** (`engine/patterns_geo.py`):
  * rectangles, ascending, descending and symmetrical triangles, fitted to **confirmed** pivots (≥ 2 touches per line within 0.5 ATR);
  * cup-and-handle: rims within 5%, a 12–35% rounded (not V) cup over 30–150 bars, and a handle ≤ ⅓ of the depth;
  * a breakout fires when the close clears the line known at the previous bar.

  Three backtested strategies use them: `geo_breakout`, `geo_breakdown` and `cup_handle`. Look-ahead tests cover every pattern column.
* **Scoring:** trend, momentum, volume, price action, structure, fundamental, volatility, regime and risk/reward, with default weights 20/15/15/15/10/10/5/5/5. Label bands are 90/75/60 and are admin-editable. The score ranks setup quality and is not a probability.
* **NO TRADE checks:** data freshness, data quality, history length, liquidity (average traded value), spread/range sanity, suspicious volume, R:R ≥ 2 to T2, stop ≤ 10%, T1 ≥ 1R, abnormal volatility, regime vs direction, MTF conflict (weekly against the trade), historical sample ≥ 30, historical expectancy > 0 after costs, earnings/event risk, and minimum score. The dashboard is never padded: if nothing passes, it shows **NO VALID SETUP**.

### 6b. Options signal architecture **[built]**

```
index bars → features → INDEX setups (price-only rules, ≤5-session hold, index-horizon levels)
           → same backtester replays them on index history → hit rates / expectancy / sample size
chain snapshot (DB) → Black-76 on the forward: IV solve, Greeks, liquidity flags
           → PCR, max pain, OI walls, OI buildup, 25Δ skew, term structure, straddle, IV percentile
market state = daily structure + regime + IV percentile (+ intraday VWAP & 5M/15M/30M/1H context)
           → contract selector → option setup (VALID / NO_TRADE)
           → strategy engine (10 strategies) → proposed / not suitable, with the conditions each failed
```

* **Direction is not enough.** Each candidate contract must pass all of:
  * liquidity: spread ≤ 3% of mid, OI ≥ 500 lots, volume ≥ 200 lots, premium ≥ ₹5;
  * ≥ 3 days to expiry;
  * |Δ| between 0.35 and 0.65;
  * theta over the hold ≤ 12% of premium;
  * modelled premium loss at the stop ≤ 50%;
  * premium R:R ≥ 1.5;
  * IV percentile < 85 for buying premium (warning from 70);
  * underlying T1 within 1.5 × the IV-implied 1σ move over the hold.

  The underlying setup must itself be VALID.
* **Premium levels** come from a Black-76 reprice of the contract at the underlying stop, T1 and T2 after the expected holding time, with IV held constant (disclosed).
* **Evidence:** option hit rates are those of the *underlying* index setup, which uses the same rules and levels on index history. Historical option prices are not used, and every setup states this in `probability.basis_note`.
* **Strategies:** long call and long put, 4 vertical spreads, long and short straddle and strangle.
  * Each has explicit gates: direction, IV percentile band, regime, ADX, squeeze, OI walls, and all legs liquid.
  * Directional structures also need a VALID underlying setup.
  * Short volatility is blocked in Panic/Selloff, on trend days and when IV isn't rich.
  * No IV-dependent strategy is proposed without an IV percentile.
* **Strategy outputs:** max profit and loss (flagging unlimited), breakevens, net Greeks, and the required capital. Capital is the net debit, the defined-risk width, or an *indicative* margin for naked shorts.
* **Probability of profit**, reported two ways:
  * *model*: risk-neutral lognormal at ATM IV;
  * *historical*: the payoff applied to every past NIFTY move over the same number of sessions, both unconditional and same-regime, with sample size and the overlap caveat.

### 6c. Multi-market architecture **[built, Phase 4]**

`engine/markets.py` holds one **profile** per market. The analysis rules are the same everywhere; only what genuinely differs between markets lives in the profile:

| Market | Calendar | Volume | Liquidity floor (20-day avg traded value) | Costs/side (comm + slip) | Strategy set | Annualisation |
|---|---|---|---|---|---|---|
| NSE | weekdays | yes | ₹5 Cr | 0.12% + 0.05% | equity (+ custom) | 252 |
| CRYPTO | **24×7**; last complete bar = yesterday (UTC) | yes | USD 20M | 0.10% + 0.05% | equity (+ custom) | 365 |
| US | weekdays | yes | USD 20M, price ≥ $5 | 0.02% + 0.03% | equity (+ custom) | 252 |
| EUROPE | weekdays | yes | USD 5M (converted from GBP/EUR) | 0.08% + 0.05% | equity (+ custom) | 252 |
| ASIA | weekdays | yes | USD 5M (converted from JPY/HKD/SGD/KRW) | 0.10% + 0.05% | equity (+ custom) | 252 |
| FX | weekdays | **no** | n/a | 0% + 0.01% (spread) | **price-only** (`px_*`) | 260 |

* **Separation.** Every market has its own benchmark and regime, and is scanned, backtested and stored separately, keyed by `instruments.market`, `scan_runs.market` and `signals.market`. Markets never share events or probabilities.
* **Currency.** `fx_service` builds conversions from the FX market's *stored* closes (direct, inverse, or bridged via USD). It uses them to:
  * normalise liquidity into the profile's currency;
  * add an **INR block** to every non-INR setup: per-unit risk/reward in ₹, a ₹10,000-risk example, and the conversion path, timestamp and sample flag.

  No hard-coded rates are used; if a rate is missing the block says unavailable.
* **Instruments without volume (FX, indices).** The volume score component is dropped and its weight redistributed (disclosed), the explanation omits volume, and traded-value liquidity does not apply.
* **Price precision** scales with magnitude: 2 dp for indices, 5 dp for EUR/USD, up to 10 dp for sub-cent tokens. This applies in levels, the backtest and the text.
* **Crypto.**
  * Perpetual-futures context: funding %/8h and annualised, OI change with a price/OI interpretation, and long/short ratio.
  * Exchanges publish only about 30 days of this history, so it is **context and warnings only**, never in the score or hit rates.
  * Spread above 20 bps blocks a setup.
  * BTC dominance is computed within the tracked universe, from provider market caps when available.
  * Liquidations and stablecoin flows are marked unavailable.
* **Forex.** Pip distances and example lots. Interest-rate differential and the macro calendar are shown as *unavailable*: nothing is fabricated.
* **Providers.**
  * `binance`: public market data only (spot klines, book ticker, funding/OI/long-short). It drops the still-forming UTC bar, filters stablecoins and leveraged tokens, and needs no key.
  * **Point-in-time universe (survivorship-bias removal).** The candidate pool is every eligible USDT pair Binance has ever listed: pairs trading now come from the API, and **delisted** pairs come from the public bulk archive (data.binance.vision, monthly zips, microsecond timestamps handled). On each date, `engine/universe.py` ranks the pool by trailing 30-bar traded value, using data up to the previous bar only, and the top `BINANCE_UNIVERSE_SIZE` (40) form that day's universe.
    * **Where membership applies:**
      * signals, *before* simulation, so the one-position-at-a-time logic stays correct;
      * breadth;
      * today's candidates;
      * backtests and ML, which use the same membership.
    * **Exclusions:**
      * USD-pegged coins are excluded by behaviour: price near 1.0 with a median daily range under 0.5%;
      * gold-backed tokens are excluded by name;
      * `BINANCE_EXCLUDE` adds any other base assets.
    * **Delisted coins:** a trade still open when a delisted coin's data ends is closed at the last close (`exit_reason: delisted`), not dropped, so crash-into-delisting losses count.
    * **Reused tickers** (for example LUNA): only the segment after a gap of more than 30 days is kept.
    * **Scaling:** derivatives context is fetched only for the current top-N.
  * `upstox` (NSE, NIFTY options): the Upstox API with the account holder's own developer app.
    * **Login:** OAuth2 authorization code. Tokens expire at 03:30 IST and there is no refresh token, so the user clicks "Connect Upstox" in Admin → Providers once a day. The callback is protected against forgery by a single-use `state` value (10 minutes). The token is stored encrypted and never returned by any endpoint. A 17:45 IST weekday reminder notifies admins if Upstox isn't connected, and ingestion stops with one clear error instead of one per symbol.
    * **History:** daily candles from the v3 API, up to 10 years per request, paced at about 1 request per second (the 30-minute cap is 2,000).
    * **Option chain:** from `/v2/option/chain`, including bid/ask, OI, previous OI (so the real OI change) and IV. A zero quote is stored as NaN.
    * **Universe:** point-in-time top-N, with the same residual survivorship caveat as Angel One.
  * `angelone` (NSE, NIFTY options): Angel One SmartAPI with the account holder's own credentials (encrypted provider keys: API_KEY, CLIENT_CODE, MPIN, TOTP_SECRET; login uses TOTP; the session token is shared through the cache).
    * **Equities:** every NSE `-EQ` stock plus Nifty 50, Nifty Bank and India VIX. Daily candles are fetched in 1,000-day windows at about 2.5 requests per second, with rate-limit back-off and a single re-login on an expired token. Today's candle is dropped before 16:00 IST.
    * **Universe:** point-in-time top 500 by traded value (`ANGEL_UNIVERSE_SIZE`). The instrument master lists only currently listed stocks, so the scan summary discloses the remaining survivorship bias.
    * **Options:** the NIFTY chain comes from FULL market quotes, covering the nearest 3 expiries and strikes within ±10% of spot. Bid and ask are taken from market depth; a missing quote is stored as NaN, never invented. `oi_change` is derived from the previous stored snapshot.
  * `twelvedata`: global equities and FX from a universe CSV. The key is stored encrypted in `api_keys`, requests are throttled to the plan's rate, and today's forming bar is dropped.
  * `sample`: synthetic data for every market, with internally consistent FX crosses.
* **Schedules (IST):** Asia 15:00, NSE 18:30 (then options), Europe 23:00, FX 03:00 and US 03:30 (Tue–Sat), crypto 06:00 daily.

### 6d. Events, news and the AI analyst **[built, Phase 5]**

* **Providers.**
  * `NewsProvider`, `EconomicCalendarProvider` and `EarningsCalendarProvider` have `sample` adapters. Sample headlines are prefixed "[SAMPLE]"; economic events use real release names on synthetic dates, marked "(sample)".
  * `finnhub` covers company and general news, the earnings calendar, and the economic calendar (paid plan; a 401/403 marks it *unavailable* rather than guessing). Its key is stored encrypted.
  * `csv` covers licensed calendar files.
* **Sentiment** (`engine/sentiment.py`):
  * a financial lexicon with phrase matching, negation handling, and title terms weighted double;
  * returns a label, a score and the **matched terms**, plus a category (earnings, regulatory, insider, analyst, macro, geopolitical, announcement);
  * never generates or scores a setup: it is shown as context, and gives a warning when ≥ 2 headlines in 3 days clearly oppose the setup's direction.
* **Event risk** (`engine/events.py`):
  * country → markets/currencies mapping (US releases also move NSE/NFO and crypto);
  * a high-impact release within 24 h **blocks** macro-sensitive setups (forex, index/option underlyings) and warns on stocks and crypto; within 3 days it warns;
  * earnings in ≤ 3 days block and ≤ 10 days warn, now for every equity market from the earnings calendar;
  * short straddles and strangles need "no high-impact release before expiry".
* **Earnings analytics:** EPS and revenue surprise %, guidance, and the historical close-to-close reaction around past reports compared with the stock's typical 2-day move.
* **AI analyst** (`services/analyst_service.py`):
  * The context pack is built only from database data: the setup (trimmed), checks, historical evidence, regime, news, earnings and events.
  * The call uses the Anthropic Messages API with `claude-opus-5-5`, effort `medium`, and a cached system prompt, with data placed in `<context>` in the user turn. The server-side refusal fallback is on (`fallbacks: "default"`).
  * The system prompt forbids invented data and certainty language, and requires sample size and period with every hit rate.
  * **Numeric grounding check:** every number in the answer must equal a context value at the answer's precision (or its % form); others are flagged as `unverified_numbers`.
  * Without a key, or on a refusal or error, it returns a **rule-based** explanation from the same context and says so.
  * Every Q&A, with its grounding result and token usage (including cache reads), is stored in `analyst_queries`. Access is premium+ with a per-user hourly limit.
* **Schedules (IST):** calendars at 05:00 daily; news hourly (07:15–23:15) and before each market's scan.

### 6e. Paper trading, portfolios, alerts and notifications **[built, Phase 7]**

* **Execution** (`engine/paper.py`, `app/brokers.py`):
  * orders go through a `BrokerAdapter`; `PaperBroker` is the only implementation, so a real broker plugs in later;
  * paper fills use the **backtester's rules**: market orders at the next bar's open plus slippage; limit/stop entries at the price, or the open when it gaps past; a stop gapped through fills at the open; when a bar touches both stop and target, the stop counts first; optional partial exit at T1, then breakeven; time exit; order expiry;
  * commission is charged on every fill and exit slippage on every exit;
  * processing is idempotent per bar;
  * a parity test confirms paper and backtest outcomes agree on random trades.
* **Portfolios:**
  * `paper` portfolios are simulated; `journal` portfolios record the user's real trades with their own brokerage and taxes;
  * valuation marks positions at the latest stored close and converts to INR at the FX rate recorded at entry and exit;
  * analytics: win rate, average winner/loser, profit factor, expectancy, max drawdown, Sharpe, holding days, open risk, and exposure by market and sector.
* **Alerts:**
  * 17 kinds: price above/below/cross, entry/target/stop reached, breakout/breakdown, support/resistance break, volume spike, RSI crosses, EMA crosses, new setup matching a filter, and unusual NIFTY options activity;
  * evaluated after each scan and every 5 minutes on the latest stored bars (end-of-day latency unless an intraday feed is configured);
  * idempotent per bar, with a cooldown and optional repeat;
  * one-click alerts from a setup (entry, T1, T2, stop).
* **Notifications:**
  * in-app (always), email (only to the account's own address), Telegram (a one-time `/start CODE` link through a webhook checked with a constant-time secret comparison, or the poller), Web Push (VAPID; dead subscriptions are pruned) and WhatsApp (Business Cloud API template, requiring E.164 format and explicit opt-in);
  * all secrets live in the encrypted provider store; unconfigured channels report `not_configured`;
  * per-channel delivery status is recorded, failed deliveries are retried every 15 minutes, and quiet hours apply to every channel except web.
* **Limits:** standard users get 1 paper and 1 journal portfolio and 10 active alerts; premium users are unlimited.

## 7. Backtesting architecture **[built]**

* **Trade simulator** (`engine/backtest.py`):
  * entry at the next open plus slippage;
  * a gap through the stop fills at the open;
  * when one bar touches both the stop and a target, the **stop is assumed first**;
  * 50% is closed at T1, then the stop moves to breakeven;
  * the rest exits at T2, breakeven or the time stop;
  * commission is charged on both legs, plus exit slippage;
  * only one open position per symbol per strategy;
  * trades still open when the data ends are **excluded** rather than marked to market.
* **Outcome flags:** each trade records `t1_hit` (T1 before the initial stop), `t2_hit`, `stop_hit` (stop before T1) and `neither` (time exit). These give the T1/T2/stop/neither rates with Wilson 95% intervals.
* **Look-ahead prevention:**
  * indicators are causal, and a test recomputes every feature on truncated history and requires equality;
  * pivots count only from their confirmation bar;
  * the weekly filter uses completed weeks only;
  * the probability lookup filters to `exit_date < as_of`.
* **Survivorship bias:** instruments trade only between `listed_on` and `delisted_on`, and delisted names stay in the sample. The sample universe includes delisted instruments to exercise this. Real survivorship-free testing needs a provider that supplies delisted history.
* **Probability similarity:** strategy + regime family + score bucket, falling back to strategy + regime, then strategy alone. The first level with ≥ 30 trades is used and is always disclosed as `conditioning`.
* **Robustness:**
  * contiguous 60/20/20 train/validation/out-of-sample segments;
  * rolling walk-forward (default 3 years train, 1 year test) that picks level parameters from an explicit grid using the training window only;
  * parameter sensitivity;
  * Monte Carlo bootstrap of the trade sequence;
  * performance by regime;
  * automatic overfit warnings (OOS expectancy collapse, too few OOS trades, fragile parameters).
* **Metrics (strategy level):** win rate, PF, expectancy (R and %), CAGR, max DD, Sharpe, Sortino, best/worst trade, monthly/yearly returns, and an R-based equity curve (fixed-fractional, booked at exit, no capital constraint). This describes the *rule*, not an account.
* **Portfolio simulator [built, Phase 3]** (`engine/portfolio.py`): a single capital account replayed day by day.
  * Sizing uses equity at the prior close: risk % of equity, capped by max position %, cash, and an optional sector cap.
  * At most `max_positions` are open, one per symbol. When signals compete for slots, the higher signal-time score wins.
  * Partial exits happen on the actual T1 date.
  * Commission is charged on every fill and slippage on every exit.
  * Equity is marked to market daily, which gives true drawdown, Sharpe, Sortino and exposure, plus a benchmark comparison.
  * Skipped trades are counted by reason, so the gap between strategy-level and portfolio-level results is explicit.
* **Robustness additions [built, Phase 3]:** per-year stability (positive-expectancy years out of years evaluated), and "too good to be true" warnings (very high PF or win rate on a small n, or an edge that is period-dependent).
* **Hit-rate explorer [built, Phase 3]:** `GET /analytics/hit-rates` exposes the exact event set behind every published probability. Filter by strategy, regime, score bucket, sector, symbol and date; group by any of those, year, direction or exit reason. Returns Wilson CIs, R-multiple/MFE/MAE/holding-time histograms and n for every figure.
* **Live track record:** published VALID setups are resolved forward with the same simulator (`signal_history`), so users can compare live outcomes with the backtest.

## 8. ML architecture **[built, Phase 6]**

* **Target:** T1 reached before the initial stop, the same outcome the empirical hit rate measures.
* **Dataset** (`engine/ml/dataset.py`):
  * one row per historical event in the latest scan of a market, using the same trades that back the published probabilities;
  * the features are read at the **signal bar only**: indicators, distances from EMAs in ATR, volatility and squeeze ranks, volume ratios, structure, planned R:R, score, breadth, regime and strategy one-hots;
  * directional features are oriented for shorts (negated or mirrored), so one model serves both directions.
* **Validation** (`engine/ml/model.py`): purged, embargoed, expanding walk-forward over 4 folds, covering the last 40% of the timeline.
  * **Train:** events that *exited* before calibration start minus the embargo (30 days, longer than the maximum hold).
  * **Calibrate:** a later window whose events exit before test start minus the embargo.
  * **Test:** the next unseen block.

  No random splits are used. Metrics come only from the concatenated out-of-sample predictions: Brier score, log loss, AUC, base rate, a 10-bin reliability table, and permutation importance.
* **Candidates and calibration:**
  * Logistic regression (standardised, L2) and gradient boosting (scikit-learn `HistGradientBoosting`, which works like LightGBM). The lowest out-of-sample Brier wins.
  * Isotonic calibration is fitted on the calibration window.
  * Neural networks are not used: they are not justified at this sample size.
* **Baseline and gate:**
  * The baseline is the platform's empirical estimate (strategy × regime-family T1 rate from the **training window only**).
  * A model is *eligible* only if all of these hold out of sample:
    * its Brier is ≤ 99% of the baseline's;
    * a **paired bootstrap** over the trades (2,000 resamples) shows it beating the baseline in ≥ 95% of resamples;
    * AUC ≥ 0.52 and n ≥ 200.
  * The bootstrap rule exists because on synthetic data one market's model cleared the 1% margin by luck (it won in only 70% of resamples).
  * Models trained before this rule must be retrained before they can be activated.
  * Activation is an admin decision on an eligible model, and it is audit-logged.
  * On the synthetic sample data the gate correctly refuses every market, because the models cannot beat the empirical rate.
* **Serving:**
  * The active model's calibrated probability is attached to each setup as `ml`, **next to** the empirical T1 rate. It shows the model version, out-of-sample Brier vs baseline, AUC and n.
  * It is also an ensemble component, `ml`, with weight 0 by default. If an admin weights it, the minimum-score rule is re-checked.
* **Drift:** each scan compares the model's live Brier with the empirical estimate's on resolved published setups (≥ 50). If the model is more than 10% worse it is **automatically suspended** and the suspension is audit-logged.
* **Persistence without pickle:**
  * Logistic models are stored as plain JSON (coefficients, scaler, medians, isotonic thresholds).
  * Gradient-boosting models use a `skops` payload loaded only after checking its types against an explicit allow-list; unexpected types are refused.
* **ML regime** (`engine/ml/regime_ml.py`):
  * duplicate state descriptions are qualified by trend strength, e.g. "Calm uptrend (strong)";
  * a 4-state Gaussian mixture on 20-day return, realised volatility, distance from the 200 EMA and ADX;
  * states are named from their own means (e.g. "Calm uptrend", "Volatile selloff") and shown with probabilities and agreement with the rule-based regime;
  * context only, not used by rules or backtests.
* **Schedules:** a weekly retrain per market (Sunday 04:30 IST) creates a new *candidate*; it is never auto-activated.

## 9. Security architecture

**[built]**
* argon2id password hashing with a password-strength policy, account lockout (5 failures → 15 min), and constant-time behaviour for unknown emails.
* JWT access tokens (15 min, HS256).
* Refresh tokens are opaque, stored **hashed**, rotated on each use. Reuse revokes the whole family, except within 10 s of a rotation that has a live successor: that case returns 409 so tabs racing each other don't log the user out. They live in an httpOnly `SameSite=Strict` cookie scoped to `/api/v1/auth`, and are `Secure` in production.
* The frontend keeps the access token in memory only.
* TOTP 2FA, with the secret Fernet-encrypted at rest.
* RBAC on every route.
* Audit logs for auth events, settings changes, jobs, strategy changes and role changes.
* Provider API keys are encrypted and never returned by any endpoint.
* Pydantic validation on all input. The strategy DSL is whitelist-only with no `eval`, and symbol path sanitisation protects the CSV provider.
* SQLAlchemy parameterised queries throughout.
* Per-IP rate limiting (stricter on login/register/2FA). `X-Forwarded-For` is honoured only from `TRUSTED_PROXIES` (the Next.js proxy), so clients cannot spoof their IP.
* Security headers: CSP, frame-deny, nosniff, referrer policy, and HSTS when secure.
* CORS allow-list.
* The production start refuses to boot with default secrets, a missing `ENCRYPTION_KEY`, or insecure cookies.
* CSRF: API calls use Bearer tokens, which are not auto-sent by browsers. The only cookie-authenticated endpoint (refresh) is `SameSite=Strict` and path-scoped.

**[built, Phase 8]**
* **Secrets from files.** Any setting can come from `NAME_FILE` (Docker/Kubernetes secrets, or cloud secret managers synced to them). An explicit environment variable wins, and a missing file fails loudly.
* **Production start-up checks.** On top of the checks above, production refuses sample (`DEMO_`) data providers unless `ALLOW_SAMPLE_IN_PRODUCTION=true`.
* **`/metrics`:**
  * needs a bearer token (compared in constant time);
  * returns 404 in production when no token is set;
  * is never routed publicly (Caddy / Ingress expose only the frontend).
* **Request IDs** are sanitised before they are echoed or logged.
* **Frontend headers.** The frontend sends a CSP. Scripts are limited to `'self'` plus Next's inline bootstrap, `connect-src 'self'`, and `frame-ancestors 'none'`. HSTS is sent in production.
* **Automated scans in CI:**
  * ruff;
  * bandit (clean);
  * pip-audit (clean on the Python 3.11 image);
  * npm audit (0 vulnerabilities; Next's bundled postcss is pinned to the patched version via `overrides`);
  * Trivy on both images, which gave 0 fixable HIGH/CRITICAL findings when run locally.
* **Minimal images.** Production images apply OS security updates at build time. They ship no packaging tools: the API image has no pip, setuptools or wheel (dev compose keeps them via `DEV_TOOLS=true`), and the frontend runtime has no npm or corepack.
* **Race-free first boot.** Start-up seeding is serialised with a Postgres advisory lock, so concurrent uvicorn workers or replicas on a fresh database don't race.
* **Containers.** They run as non-root with a read-only root filesystem, all capabilities dropped and no privilege escalation. Kubernetes adds Pod Security `restricted`, default-deny NetworkPolicies and no ServiceAccount token.
* **Retention.** Audit logs are kept 365 days and notifications 90 days (both configurable); finished job records are pruned after 90 days.

**Still recommended (outside the codebase):**
* a WAF at the edge;
* per-user rate limits (today they are per-IP);
* periodic penetration tests.

## 10. Deployment architecture **[built, Phase 8]**

The full guide is in [DEPLOYMENT.md](DEPLOYMENT.md), and the runbook is in [OPERATIONS.md](OPERATIONS.md).

**Processes:**
* `api`: stateless, uvicorn × `WEB_CONCURRENCY`, with `/health` (liveness), `/ready` (DB, Redis, migrations at head) and `/metrics`.
* Celery workers, one per queue:
  * `default` + `notifications` (prefork);
  * `scans` (solo pool, forks `SCAN_WORKERS` per-symbol processes);
  * `backtests` (solo pool, likewise).
* `beat` (exactly one).
* `frontend` (Next.js standalone; the only public service; proxies `/api/v1`).
* Postgres 16 and Redis.

**Targets:**
* **Dev:** `docker-compose.yml`.
* **Single host:** `docker-compose.prod.yml`, with Caddy for automatic TLS, only 80/443 published, an internal data network, file-based secrets and a nightly `pg_dump`.
* **Kubernetes:** `deploy/k8s` kustomize base plus a production overlay:
  * a migrations Job;
  * HPAs and PDBs;
  * NetworkPolicies;
  * a backup CronJob;
  * cert-manager TLS.

  The AWS (EKS, RDS, ElastiCache, ALB, Secrets Manager, EFS) and Azure (AKS, Flexible Server, Azure Cache, Key Vault, Azure Files) mappings are in DEPLOYMENT.md.

**Observability:**
* JSON logs with request IDs;
* Prometheus metrics: HTTP from the API plus job, scan, data-lag, notification and ML gauges computed from Postgres, which cover the workers;
* alert rules and a Grafana dashboard in `ops/`;
* optional Sentry.

**CI (GitHub Actions):**
* lint;
* migrations up/down/up on Postgres;
* tests;
* bandit;
* pip-audit;
* frontend type-check, lint, build and audit;
* manifest render;
* image builds with Trivy scans (API and frontend), pushed to GHCR on `main`.

## 11. Development phases

| Phase | Scope | Status |
|---|---|---|
| 1 + MVP core of 3 | auth/RBAC/2FA, dashboard, market data pipeline, NSE swing scanner, indicators, setups, entry/stop/targets, scoring, regime, breadth, sectors, historical target-hit engine, backtesting + walk-forward, NO TRADE engine, watchlists, risk calculator, charts, admin | **built** |
| 2 | NIFTY options: chain ingestion, Greeks (Black-76), IV percentile, OI/PCR/max pain/buildup, liquidity/spread filters, intraday VWAP context, contract selector, strategy engine (10 strategies) with payoff, breakevens, capital and model/historical POP, payoff builder | **built** |
| 3 | portfolio simulator (MTM, capital/slot/sector constraints), rule DSL v2 (OR groups, scaled/lagged operands, custom exits), immutable strategy versions, custom strategies in the daily scan, hit-rate explorer, yearly stability & too-good warnings, backtest compare + CSV export | **built** |
| 4 | market registry (NSE, CRYPTO, US, EUROPE, ASIA, FX) with per-market calendar/liquidity/costs/strategy set; crypto scanner with derivatives context; global equities with INR conversion; forex with pips and price-only rules; Binance + Twelve Data adapters; global dashboard; per-market schedules | **built** |
| 5 | news + explainable sentiment (context only), earnings calendar with surprises and historical reaction, economic calendar with market/currency mapping, event-risk checks (macro + earnings + news) in every market and in options strategy gating, grounded AI analyst with numeric verification and audit log | **built** |
| 6 | ML layer (purged walk-forward, isotonic calibration, baseline gate, drift auto-suspend, pickle-free persistence), ML regime model, ensemble components (historical evidence + ML, weight 0 by default), geometric patterns as backtested strategies | **built** |
| 7 | paper trading (backtest-identical fills), trade journal, portfolio analytics, broker adapter interface, 17 alert kinds, notifications (web, email, Telegram, Web Push, WhatsApp template) with encrypted credentials, quiet hours and retries | **built** |
| 8 | per-symbol parallel scans/backtests (identical output), queue-per-workload Celery, response caching + post-scan prewarm, hot-path indexes, optional partitioning; JSON logs + request IDs, Prometheus metrics/alerts/dashboard, readiness, Sentry; file-based secrets, production data guard, CSP, retention, backups/restore; Kubernetes + single-host TLS compose + CI with security scans; load-test harness | **built** |

## 12. Required API keys / services

| Need | Required for | Examples (verify licence terms) |
|---|---|---|
| NSE/BSE EOD + intraday OHLCV, index constituents, corporate actions, **delisted history** | real Indian scanning | authorised NSE vendors; broker APIs (Kite Connect, Upstox, SmartAPI) |
| NSE F&O option chain with OI/IV | Phase 2 | broker API / vendor |
| Fundamentals & shareholding | Hybrid mode | licensed fundamentals vendor |
| Earnings/corporate-action calendar | event-risk checks | vendor / exchange feeds |
| Crypto spot + perpetuals | Phase 4 | exchange public APIs |
| US/EU/Asia equities, FX, USDINR | Phase 4 | Polygon, Twelve Data, EODHD, Tiingo |
| News | Phase 5 | licensed news API |
| LLM for the AI analyst | Phase 5 | Anthropic Claude API |
| SMTP / Telegram bot / Web Push VAPID keys | Phase 7 | |

## 13. Estimated infrastructure

| Stage | Sizing |
|---|---|
| Dev / MVP | 1 VM with 4 vCPU and 8 GB RAM; Postgres 20 GB. |
| Production (~5k users, 3 markets) | API 2–4 × (1 vCPU, 2 GB); `worker-scans` and `worker-backtests` 1–2 × (2–4 vCPU, 6 GB); default worker 1 × (0.5 vCPU, 1 GB); Postgres 4 vCPU / 16 GB / 200 GB SSD (~50–100M bar rows including intraday); Redis 1–2 GB. Market data licences will cost more than the compute. |

**Measured, Phase 8** (Docker on an 8-CPU laptop, sample data).

*Scan engine (NSE universe, features + event replay).* Output is identical in both runs (5,447 trades). A 2,000-symbol universe scales roughly linearly per symbol.

| `SCAN_WORKERS` | Time |
|---|---|
| 1 | 9.6 s |
| 4 | 2.9 s |

*Load test.* One API container with 2 uvicorn workers; 20 users with 50–250 ms think time for 60 s (`scripts/loadtest.py`). There were 0 errors in every run.

| Run | Throughput | Overall p95 | Detail |
|---|---|---|---|
| Before caching | 62 req/s | 906 ms | Candles p50 680 ms: features were recomputed on every request. Cold `/analysis` p95 2.5 s. |
| After caching, cold cache (just after a scan) | 95 req/s | 167 ms | p99 944 ms, from first-time `/analysis` on symbols that were not pre-warmed |
| After caching, warm | 120 req/s | 31 ms | Every endpoint p95 ≤ 44 ms |

## 14. Testing strategy

* **Phase 8 [built]:**
  * request-ID generation and sanitisation;
  * JSON log format;
  * readiness checks;
  * `/metrics` exposes business gauges, requires the token when one is set, and is disabled in production without one;
  * `*_FILE` secrets (precedence, loud failure);
  * the production sample-data guard;
  * **parallel == serial** for features and events (real `fork` in the Linux image and CI);
  * worker-count rules;
  * Celery routing for every queue, with every beat task registered;
  * retention (running jobs kept);
  * scan pre-warm and candles cache keyed on the last bar, with freshness never cached.

* **Engine correctness [built]:**
  * look-ahead tests: every feature and every strategy signal must be identical on truncated history;
  * pivot confirmation timing;
  * indicator reference values;
  * trade-simulator edge cases (same-bar stop/target, gaps, chase skip, time exit, unresolved exclusion, costs, shorts);
  * level ordering;
  * probability point-in-time filtering and disclosed fallback;
  * Wilson intervals;
  * NO TRADE blocking (stale data, low sample, negative expectancy);
  * regime detection;
  * disjoint walk-forward windows;
  * risk maths (the brief's ₹5,00,000 example);
  * DSL injection rejection.
* **API integration [built]:**
  * auth (weak passwords, rotation plus reuse detection, lockout, 2FA);
  * RBAC and tier limits;
  * the full ingest → scan → signals flow on SAMPLE data, asserting sample labelling, disclaimers, and that no probability appears without its sample size;
  * stocks, charts and analysis endpoints;
  * watchlists (ownership isolation);
  * backtests (built-in with walk-forward, and custom DSL);
  * admin settings plus audit;
  * secrets never echoed;
  * security headers.
* **Options [built]:**
  * pricing: Black-76 reference value, put-call parity, IV round trip, Greeks vs finite differences, and the 15:30 IST year fraction;
  * chain: max pain against a hand computation, PCR and OI walls, buildup labels, the IV-percentile history minimum;
  * payoffs: exact max P/L and breakevens for spreads, straddles and puts, plus historical-POP sample sizes;
  * selector: delta/DTE/liquidity choice, blocking on rich IV, an invalid underlying or illiquid contracts;
  * strategy gating: range + rich IV, panic, bullish, unknown IV;
  * API: permissions, chain, payoff builder, and option setups never leaking into equity lists.
* **Phase 3 [built]:**
  * DSL v2: mult/shift semantics, OR groups, no negative shift, limits, custom exits driving levels, and look-ahead parity;
  * portfolio: risk sizing and P&L, max positions with score priority, same-day stop resolution, partial exits and costs, intra-trade MTM drawdown;
  * explorer grouping and filters;
  * API: version history immutability, a backtest pinned to v1 vs v2, portfolio invariants (taken + skipped = available, ≤ max positions, nothing left open), compare, CSV ownership, a custom strategy included in the scan producing performance and explorer data.
* **Phase 4 [built]:**
  * calendars (weekday and 24×7);
  * price precision and pips;
  * volume-less scoring;
  * derivatives interpretation and warnings;
  * FX book (direct, inverse, USD bridge, sample flag propagation);
  * sample invariants: FX crosses equal the product of their legs; crypto includes weekends and ends at the last complete day;
  * Binance and Twelve Data adapters against mocked HTTP: universe filtering, spread, forming-bar drop, funding daily averaging, vendor errors, missing key;
  * multi-market API: listing, global overview, per-market signal endpoints (INR, pips and derivatives present; markets never mix), market-scoped analysis, crypto backtest using crypto costs and 365-day annualisation, FX explorer.
* **Phase 5 [built]:**
  * sentiment labels, categories and negation;
  * macro block-vs-warn rules and market/currency relevance;
  * news-opposition warnings;
  * earnings-reaction statistics;
  * Finnhub adapter (mocked, including plan-restricted endpoints);
  * sample-calendar invariants;
  * options short-vol gating on a release before expiry;
  * OI-buildup keys including expiry (regression test);
  * analyst: grounding check (flags invented numbers), LLM path with a fake client asserting model, fallback, beta header, cache control and effort; refusal and no-key fallbacks; audit history; permissions;
  * news, calendar and stock-events API.

  The real SDK request shape is verified in the Docker image (anthropic 1.9) through a mocked HTTP transport; no paid API calls are made in tests.
* **Phase 6 [built]:**
  * patterns: ascending-triangle breakout, cup-and-handle, and look-ahead parity for pattern columns;
  * ML: purged splits never overlap labels; a synthetic dataset with a real signal passes the gate while pure noise does not; isotonic calibration corrects a biased model; the baseline uses the training window only; the gate's minimum out-of-sample size;
  * artifacts: JSON and skops round trips, and a skops payload with an unexpected type is refused;
  * ML regime determinism and naming; ensemble components (weight 0 leaves scores unchanged);
  * lifecycle API: train → gate refusal (409) → activation → scan annotation → drift auto-suspension.
* **Phase 7 [built]:**
  * paper execution: next-open fills, limit/stop entries including gaps, gap-through stop, same-bar stop priority, partial exit with breakeven, expiry, time exit, manual close, costs, idempotency, and parity with the backtester;
  * alert conditions, the unusual-options detector, and journal analytics;
  * channels, all mocked: email only to the account address, Telegram, Web Push pruning, WhatsApp not configured; quiet hours;
  * API: full paper flow (pending → filled at the next open with slippage → T1 partial → close at the next open), journal validation, alert CRUD with validation and limits, per-bar idempotency, notifications read and unread, alerts from a setup, settings (E.164 + opt-in), Telegram link through the webhook secret (single-use code), push subscription, test notification.
* **Migrations:** `alembic upgrade head` and `alembic check` against Postgres 16 (no model drift).
* **Next:** property-based tests for the simulator; golden-file regression of scan output per engine version; frontend Playwright smoke tests at 360 px and desktop; load tests on `/signals/top` and `/stocks/{s}/analysis`; CI running all of it on every PR.
