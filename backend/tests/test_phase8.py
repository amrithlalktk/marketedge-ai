"""Phase 8: observability, readiness, secrets from files, production guards, parallelism, queue routing, retention."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.core.config import Settings, get_settings


# ------------------------------------------------------------------ request id / logging
def test_request_id_generated_and_echoed(app_client):
    r = app_client.get("/health")
    rid = r.headers.get("X-Request-ID")
    assert rid and len(rid) == 32
    r = app_client.get("/health", headers={"X-Request-ID": "client-trace-12345"})
    assert r.headers["X-Request-ID"] == "client-trace-12345"


def test_unsafe_request_id_replaced(app_client):
    r = app_client.get("/health", headers={"X-Request-ID": "bad id\r\ninjected"})
    assert r.headers["X-Request-ID"] != "bad id\r\ninjected" and len(r.headers["X-Request-ID"]) == 32


def test_json_formatter_includes_request_id():
    from app.core.observability import JsonFormatter, request_id_var

    tok = request_id_var.set("abc123def456")
    try:
        rec = logging.LogRecord("x", logging.INFO, __file__, 1, "hello %s", ("world",), None)
        rec.status = 200
        out = json.loads(JsonFormatter().format(rec))
    finally:
        request_id_var.reset(tok)
    assert out["message"] == "hello world" and out["request_id"] == "abc123def456" and out["status"] == 200 and out["level"] == "INFO"


# ------------------------------------------------------------------ readiness / metrics
def test_ready_reports_checks(app_client):
    r = app_client.get("/ready")
    body = r.json()
    assert body["checks"]["database"] == "ok"
    assert "migrations" in body["checks"]
    # the test DB is created with create_all (no alembic_version) → migrations unknown, which does not block readiness
    assert r.status_code == (200 if body["ready"] else 503)








# ------------------------------------------------------------------ settings
def test_file_secrets(tmp_path, monkeypatch):
    from app.core import config

    f = tmp_path / "secret_key"
    f.write_text("from-a-file-secret-value-that-is-long-enough\n")
    monkeypatch.setenv("SECRET_KEY_FILE", str(f))
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("NOT_A_SETTING_FILE", str(f))  # ignored
    assert config._file_secrets() == {"secret_key": "from-a-file-secret-value-that-is-long-enough"}
    # explicit env var wins over the file
    monkeypatch.setenv("SECRET_KEY", "explicit")
    assert "secret_key" not in config._file_secrets()


def test_file_secret_missing_file_fails_loudly(monkeypatch):
    from app.core import config

    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.setenv("SMTP_PASSWORD_FILE", "/nonexistent/secret")
    with pytest.raises(FileNotFoundError):
        config._file_secrets()


def _prod(**kw):
    base = dict(environment="production", secret_key="x" * 40, encryption_key="k", cookie_secure=True)
    return Settings(**{**base, **kw})


def test_production_refuses_sample_data():
    with pytest.raises(RuntimeError, match="sample"):
        _prod().validate_production()
    _prod(allow_sample_in_production=True).validate_production()
    live = {k: "csv" for k in ("market_data_provider", "options_data_provider", "crypto_data_provider",
                               "global_data_provider", "fx_data_provider")}
    _prod(**live, news_provider="none", calendar_provider="none").validate_production()


# ------------------------------------------------------------------ parallelism
def _bars(n_sym=10, n=420, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-04", periods=n)
    out = {}
    for i in range(n_sym):
        close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.018, n)))
        opn = close * (1 + rng.normal(0, 0.004, n))
        hi = np.maximum(opn, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
        lo = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
        out[f"T{i:02d}"] = pd.DataFrame({"open": opn, "high": hi, "low": lo, "close": close,
                                         "volume": rng.integers(2e5, 2e6, n).astype(float)}, index=idx)
    return out


def test_parallel_equals_serial():
    from engine.analyzer import Analyzer

    bars = _bars()
    runs = []
    for w in (1, 4):
        a = Analyzer(workers=w)
        feats = a.prepare(bars)
        ctx = a.market_context(bars["T00"], feats)
        ev = a.build_events(feats, ctx.regime_df)
        runs.append((feats, ev))
    (f1, e1), (f4, e4) = runs
    assert list(f1) == list(f4)
    for k in f1:
        pd.testing.assert_frame_equal(f1[k], f4[k])
    assert len(e1) > 0
    pd.testing.assert_frame_equal(e1.reset_index(drop=True), e4.reset_index(drop=True))


def test_effective_workers_rules(monkeypatch):
    from engine import parallel

    assert parallel.effective_workers(1, 100) == 1
    assert parallel.effective_workers(4, 3) == 1  # too few items to be worth forking
    monkeypatch.setattr(parallel, "can_fork", lambda: False)
    assert parallel.effective_workers(8, 100) == 1
    monkeypatch.setattr(parallel, "can_fork", lambda: True)
    assert 1 <= parallel.effective_workers(8, 100) <= 8


# ------------------------------------------------------------------ queues / retention


def test_retention_cleanup(app_client):
    from app.core.db import SessionLocal
    from app.models import AuditLog, Job
    from app.jobs import retention as retention_cleanup_db

    db = SessionLocal()
    try:
        old = datetime.now(timezone.utc) - timedelta(days=400)
        db.add(AuditLog(action="test.old", created_at=old))
        db.add(AuditLog(action="test.new"))
        db.add(Job(kind="test", status="done", created_at=old))
        db.add(Job(kind="test-running", status="running", created_at=old))
        db.commit()
        out = retention_cleanup_db(db)
        assert out["audit_logs"] >= 1 and out["jobs"] >= 1
        actions = {a for (a,) in db.query(AuditLog.action).filter(AuditLog.action.like("test.%"))}
        assert actions == {"test.new"}
        assert db.query(Job).filter(Job.kind == "test-running").count() == 1  # running jobs are never deleted
    finally:
        db.close()


# ------------------------------------------------------------------ caching
def test_scan_prewarms_and_candles_cache_tracks_last_bar(app_client, admin_headers, scanned):
    from datetime import date as _date

    from app.core.cache import get_cache
    from app.core.db import SessionLocal
    from app.models import DataStatus, Instrument
    from app.services.scan_service import latest_run

    # a watchlisted stock is always pre-warmed (the synthetic data may produce no setup on some dates)
    wl = app_client.post("/api/v1/watchlists", json={"name": "Prewarm check"}, headers=admin_headers).json()
    app_client.post(f"/api/v1/watchlists/{wl['id']}/items", json={"symbol": "DEMO_001"}, headers=admin_headers)
    # run a scan here: other tests (settings changes, rescans) clear the cache after the session scan
    r = app_client.post("/api/v1/admin/jobs/scan?market=NSE", headers=admin_headers)
    assert app_client.get(f"/api/v1/admin/jobs/{r.json()['job_id']}", headers=admin_headers).json()["status"] == "done"
    db = SessionLocal()
    try:
        run = latest_run(db, "NSE")
        from app.models import Signal

        sg = db.query(Signal).filter(Signal.scan_run_id == run.id).order_by((Signal.status != "VALID"), Signal.score.desc()).first()
        sym = sg.symbol if sg else "DEMO_001"
        assert get_cache().get_json(f"me:analysis:{sym}:hybrid:{run.id}:{_date.today()}") is not None  # warmed by the scan
        r1 = app_client.get(f"/api/v1/stocks/{sym}/candles", headers=admin_headers).json()
        ins = db.query(Instrument).filter(Instrument.symbol == sym).one()
        st = db.get(DataStatus, (ins.id, "1d"))
        key = f"me:candles:{sym}:1d:500:{st.last_bar_ts}:{st.fetched_at}"
        assert get_cache().get_json(key)["bars"]["t"] == r1["bars"]["t"]
        assert "data" in r1 and "data" not in get_cache().get_json(key)  # freshness is never cached
    finally:
        db.close()


def test_admin_health_includes_readiness_and_queues(app_client, admin_headers):
    d = app_client.get("/api/v1/admin/health", headers=admin_headers).json()
    assert d["readiness"]["checks"]["database"] == "ok"
    assert "queues" not in d  # no queue in the serverless build


# ------------------------------------------------------------------ sample → live switch
def test_switching_market_to_live_provider_deactivates_sample_instruments(app_client):
    from app.core.db import SessionLocal
    from app.models import Instrument as InstrumentRow
    from app.providers.base import DataMeta, Instrument, MarketDataProvider
    from app.services.market_data import ingest, instrument_maps

    class FakeLive(MarketDataProvider):
        name, is_sample = "fake-live", False

        def list_instruments(self, market="ZZ"):
            return [Instrument("LIVEAUSDT", "LiveA", "CRYPTO", "TEST", "USD", None)]

        def get_ohlcv(self, symbol, interval="1d", start=None, end=None):
            assert not symbol.startswith("DEMO_"), "sample symbols must never be requested from a live provider"
            idx = pd.bdate_range("2026-01-01", periods=5)
            df = pd.DataFrame({"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "volume": 10.0}, index=idx)
            return df, DataMeta("fake-live", False, str(idx[-1].date()), "now")

    db = SessionLocal()
    try:
        db.add(InstrumentRow(symbol="DEMO_ZZ1", name="demo", asset_class="CRYPTO", exchange="X", currency="USD", market="ZZ", is_sample=True))
        db.commit()
        stats = ingest(db, FakeLive(), "ZZ")
        assert stats["instruments"] == 1 and not stats["errors"]
        ids, info = instrument_maps(db, "ZZ")
        assert set(info) == {"LIVEAUSDT"}  # the scan universe is live-only
        assert db.query(InstrumentRow).filter_by(symbol="DEMO_ZZ1").one().is_active is False  # kept, not deleted
    finally:
        db.close()




def test_strategy_disabled_per_market_blocks_live_setups_only(app_client, admin_headers):
    from app.core.db import SessionLocal
    from app.services.scan_service import enrich_setups

    r = app_client.put("/api/v1/admin/strategy-controls/NSE/support_bounce", headers=admin_headers,
                       json={"enabled": False, "reason": "negative expectancy on point-in-time history"})
    assert r.status_code == 200 and "support_bounce" in r.json()["disabled"]
    assert app_client.put("/api/v1/admin/strategy-controls/NSE/nope", headers=admin_headers, json={"enabled": False}).status_code == 404
    assert app_client.put("/api/v1/admin/strategy-controls/MARS/support_bounce", headers=admin_headers, json={"enabled": False}).status_code == 404
    listed = {s["id"]: s for s in app_client.get("/api/v1/admin/strategies?market=NSE", headers=admin_headers).json()["items"]}
    assert listed["support_bounce"]["disabled"] and listed["breakout_volume"]["disabled"] is None
    db = SessionLocal()
    try:
        mk = lambda sid: {"symbol": "X", "strategy_id": sid, "strategy_name": sid, "current_price": 10.0, "stop": 9.0, "targets": [11.5, 13.0],  # noqa: E731
                          "direction": "LONG", "status": "VALID", "checks": [], "explanation": {"risk_factors": []}}
        a, b = mk("support_bounce"), mk("breakout_volume")
        enrich_setups(db, [a, b], "NSE", {"X": {"id": 1, "currency": "INR", "meta": {}, "is_index": False, "is_sample": True}}, {})
        assert a["status"] == "NO_TRADE" and any(c["name"] == "Strategy enabled" and "negative expectancy" in c["detail"] for c in a["checks"])
        assert b["status"] == "VALID"
    finally:
        db.close()
    audit = app_client.get("/api/v1/admin/audit-logs", headers=admin_headers).json()
    assert any(x["action"] == "strategy.disable" for x in audit.get("items", audit))
    assert app_client.put("/api/v1/admin/strategy-controls/NSE/support_bounce", headers=admin_headers, json={"enabled": True}).json()["disabled"] == {}






def test_instrument_missing_from_full_master_is_delisted_not_retried(app_client):
    from app.core.db import SessionLocal
    from app.models import DataStatus
    from app.models import Instrument as InstrumentRow
    from app.providers.base import DataMeta, Instrument, MarketDataProvider
    from app.services.market_data import ingest

    class Master(MarketDataProvider):
        name, is_sample, lists_full_universe = "master", False, True

        def __init__(self, syms):
            self.syms, self.requested = syms, []

        def list_instruments(self, market="ZQ"):
            return [Instrument(s, s, "EQUITY", "NSE", "INR", None) for s in self.syms]

        def get_ohlcv(self, symbol, interval="1d", start=None, end=None):
            self.requested.append(symbol)
            idx = pd.bdate_range("2026-01-05", periods=5)
            return pd.DataFrame({"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "volume": 10.0}, index=idx), DataMeta("master", False, "x", "now")

    db = SessionLocal()
    try:
        ingest(db, Master(["AAA1", "GONE1"]), "ZQ")
        p = Master(["AAA1"])                       # GONE1 has left the exchange
        out = ingest(db, p, "ZQ")
        gone = db.query(InstrumentRow).filter_by(symbol="GONE1").one()
        assert gone.delisted_on == date(2026, 1, 9)        # its last stored bar
        assert p.requested == ["AAA1"] and not out["errors"]  # not requested, no daily error
        assert db.get(DataStatus, (gone.id, "1d")).last_bar_ts is not None  # history kept for backtests
    finally:
        db.close()








def test_sample_results_hidden_once_market_uses_real_data(app_client, admin_headers, scanned, monkeypatch):
    from app.core.cache import get_cache
    from app.core.db import SessionLocal
    from app.services.scan_service import latest_run

    db = SessionLocal()
    try:
        assert latest_run(db, "NSE") is not None                       # sample provider: sample scans are current
        monkeypatch.setattr(get_settings(), "market_data_provider", "upstox")
        monkeypatch.setattr(get_settings(), "options_data_provider", "upstox")
        get_cache().invalidate_prefix("me:")
        assert latest_run(db, "NSE") is None                           # switched to real data: old DEMO scan never shown
        top = app_client.get("/api/v1/signals/top?market=NSE", headers=admin_headers)
        assert top.status_code == 404 or not top.json().get("items")
        assert app_client.get("/api/v1/markets/overview?market=NSE", headers=admin_headers).status_code == 404
        assert app_client.get("/api/v1/options/signals", headers=admin_headers).status_code == 404
    finally:
        get_cache().invalidate_prefix("me:")
        db.close()


def test_pool_cap_keeps_top_by_traded_value_and_only_grows(app_client):
    from app.core.db import SessionLocal
    from app.models import Instrument as InstrumentRow
    from app.providers.base import DataMeta, Instrument, MarketDataProvider
    from app.services.market_data import ingest

    class Capped(MarketDataProvider):
        name, is_sample, lists_full_universe = "capped", False, True

        def __init__(self, ranking, cap=2):
            self.ranking, self.pool_cap, self.requested = ranking, cap, []

        def list_instruments(self, market="ZC"):
            return [Instrument("IDXZC", "idx", "INDEX", "NSE", "INR", None, is_index=True)] + \
                   [Instrument(s, s, "EQUITY", "NSE", "INR", None) for s in ("AZC", "BZC", "CZC", "DZC")]

        def rank_by_traded_value(self, symbols):
            return [s for s in self.ranking if s in symbols]

        def get_ohlcv(self, symbol, interval="1d", start=None, end=None):
            self.requested.append(symbol)
            idx = pd.bdate_range("2026-01-05", periods=5)
            return pd.DataFrame({"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "volume": 10.0}, index=idx), DataMeta("capped", False, "x", "now")

    db = SessionLocal()
    try:
        p = Capped(["CZC", "AZC", "BZC", "DZC"])
        ingest(db, p, "ZC")
        assert set(p.requested) == {"IDXZC", "CZC", "AZC"}                    # index + top 2 only
        assert db.query(InstrumentRow).filter_by(symbol="BZC").one().is_active is False   # parked, not deleted
        p2 = Capped(["DZC", "BZC", "CZC", "AZC"])                              # ranking changed
        ingest(db, p2, "ZC")
        assert set(p2.requested) == {"IDXZC", "CZC", "AZC", "DZC", "BZC"}       # pool only grows: keeps C, A; adds D, B
    finally:
        db.close()


def test_bootstrap_admin_gets_daily_ideas_alert_once(app_client):
    from app.cli import ensure_admin, ensure_daily_ideas_alert
    from app.core.db import SessionLocal
    from app.models import Alert

    db = SessionLocal()
    try:
        u = ensure_admin(db, "boot@example.com", "B00t!Strong-Pass")
        ensure_daily_ideas_alert(db, u)
        ensure_daily_ideas_alert(db, u)
        assert db.query(Alert).filter_by(user_id=u.id, kind="daily_ideas").count() == 1
    finally:
        db.close()


def test_sample_digest_does_not_block_the_real_one(app_client, scanned, monkeypatch):
    """A SAMPLE 'daily ideas' message sent before the switch to real data must not count as today's real message."""
    from app.cli import ensure_admin, ensure_daily_ideas_alert
    from app.core.db import SessionLocal
    from app.services import scan_service
    from app.services.alert_service import daily_ideas_alerts

    db = SessionLocal()
    try:
        ensure_daily_ideas_alert(db, ensure_admin(db, "digest@example.com", "D1gest!Strong-Pass"))
        monkeypatch.setattr(scan_service, "matches_provider", lambda market, is_sample: True)
        monkeypatch.setattr(scan_service, "provider_is_sample", lambda market: True)
        daily_ideas_alerts(db)
        assert daily_ideas_alerts(db) == 0  # once per session
        monkeypatch.setattr(scan_service, "provider_is_sample", lambda market: False)
        assert daily_ideas_alerts(db) >= 1  # real data: sent again for the same session date
        assert daily_ideas_alerts(db) == 0
    finally:
        db.close()


def test_database_url_accepts_neon_style_prefixes():
    for raw in ("postgres://u:p@h/db?sslmode=require", "postgresql://u:p@h/db?sslmode=require", "postgresql+psycopg://u:p@h/db?sslmode=require"):
        assert Settings(database_url=raw).database_url == "postgresql+psycopg://u:p@h/db?sslmode=require"
    assert Settings(database_url="sqlite:///x.db").database_url == "sqlite:///x.db"


def test_production_misconfiguration_is_reported_not_crashed(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    s = get_settings()
    monkeypatch.setattr(s, "environment", "production")
    monkeypatch.setattr(s, "encryption_key", None)
    r = TestClient(create_app()).get("/api/v1/auth/config")
    assert r.status_code == 503 and "ENCRYPTION_KEY" in r.json()["detail"]


def test_vercel_production_defaults(monkeypatch):
    from app.core import config

    for k in ("ENVIRONMENT", "MARKET_DATA_PROVIDER", "CORS_ORIGINS", "GITHUB_REPO", "ALLOW_REGISTRATION"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "me-app.vercel.app")
    monkeypatch.setenv("VERCEL_GIT_REPO_OWNER", "someone")
    monkeypatch.setenv("VERCEL_GIT_REPO_SLUG", "marketedge-ai")
    monkeypatch.setenv("CRYPTO_DATA_PROVIDER", "sample")            # an explicit value still wins
    s = config.Settings(**config._vercel_defaults())
    assert s.environment == "production" and s.market_data_provider == "upstox" and s.crypto_data_provider == "sample"
    assert s.cors_origins == ["https://me-app.vercel.app"] and s.upstox_redirect_uri == "https://me-app.vercel.app/api/v1/upstox/callback"
    assert s.github_repo == "someone/marketedge-ai" and s.allow_registration is False and s.job_runner == "github" and s.cookie_secure
    monkeypatch.setenv("VERCEL_ENV", "preview")
    assert config._vercel_defaults() == {}


def test_live_quotes(app_client, admin_headers, scanned, monkeypatch):
    """Display-only live prices: none for SAMPLE markets, cached, and a quote outage is reported, not raised."""
    from app.core.cache import get_cache
    from app.services import live_quotes, scan_service

    r = app_client.get("/api/v1/signals/live-quotes?items=NSE:DEMO_NIFTY50", headers=admin_headers).json()
    assert r["quotes"] == {} and r["errors"] == {}  # sample data: nothing live to show

    monkeypatch.setattr(scan_service, "provider_is_sample", lambda market: False)
    monkeypatch.setattr(live_quotes, "_nse", lambda db, syms: {s: 101.5 for s in syms})
    monkeypatch.setattr(live_quotes, "_crypto", lambda syms: (_ for _ in ()).throw(RuntimeError("binance down")))
    get_cache().invalidate_prefix("live:")
    r = app_client.get("/api/v1/signals/live-quotes?items=NSE:GRAPHITE,CRYPTO:BTCUSDT", headers=admin_headers).json()
    assert r["quotes"]["NSE:GRAPHITE"]["price"] == 101.5 and "CRYPTO" in r["errors"] and "CRYPTO:BTCUSDT" not in r["quotes"]
    assert app_client.get("/api/v1/signals/live-quotes?items=NSE:GRAPHITE", headers={}).status_code == 401


def test_nse_session_hours():
    from datetime import datetime

    from app.providers.upstox import IST
    from app.services.live_quotes import nse_open

    assert nse_open(datetime(2026, 10, 6, 10, 0, tzinfo=IST)) and not nse_open(datetime(2026, 10, 6, 16, 0, tzinfo=IST))
    assert not nse_open(datetime(2026, 10, 4, 10, 0, tzinfo=IST))  # Sunday
