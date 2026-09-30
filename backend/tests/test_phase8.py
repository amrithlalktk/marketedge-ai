"""Phase 8: observability, readiness, secrets from files, production guards, parallelism, queue routing, retention."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

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


def test_metrics_open_outside_production_and_has_business_gauges(app_client, scanned):
    r = app_client.get("/metrics")
    assert r.status_code == 200
    text = r.text
    assert "marketedge_http_requests_total" in text
    assert "marketedge_jobs_24h" in text
    assert 'marketedge_scan_valid_setups{market="NSE"}' in text


def test_metrics_token_required_when_set(app_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "metrics_token", "s3cret-metrics-token")
    assert app_client.get("/metrics").status_code == 404
    assert app_client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 404
    assert app_client.get("/metrics", headers={"Authorization": "Bearer s3cret-metrics-token"}).status_code == 200


def test_metrics_disabled_in_production_without_token(monkeypatch):
    from app.core.observability import metrics_allowed

    monkeypatch.setattr(get_settings(), "environment", "production")
    monkeypatch.setattr(get_settings(), "metrics_token", None)
    assert metrics_allowed("") is False


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

    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setenv("FINNHUB_API_KEY_FILE", "/nonexistent/secret")
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
def test_task_routing():
    from app.workers.celery_app import celery

    route = lambda name: celery.amqp.router.route({}, name)["queue"].name  # noqa: E731
    assert route("app.workers.tasks.scan") == "scans"
    assert route("app.workers.tasks.ingest_and_scan") == "scans"
    assert route("app.workers.tasks.backtest") == "backtests"
    assert route("app.workers.tasks.ml_train") == "backtests"
    assert route("app.workers.tasks.alerts_tick") == "notifications"
    assert route("app.workers.tasks.news") == "default"
    assert route("app.workers.tasks.retention_cleanup") == "default"
    # every task scheduled by beat is registered
    for entry in celery.conf.beat_schedule.values():
        assert entry["task"] in celery.tasks, entry["task"]


def test_retention_cleanup(app_client):
    from app.core.db import SessionLocal
    from app.models import AuditLog, Job
    from app.workers.tasks import retention_cleanup_db

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

    # run a scan here: other tests (settings changes, rescans) clear the cache after the session scan
    r = app_client.post("/api/v1/admin/jobs/scan?market=NSE", headers=admin_headers)
    assert app_client.get(f"/api/v1/admin/jobs/{r.json()['job_id']}", headers=admin_headers).json()["status"] == "done"
    db = SessionLocal()
    try:
        run = latest_run(db, "NSE")
        from app.models import Signal

        sg = db.query(Signal).filter(Signal.scan_run_id == run.id).order_by((Signal.status != "VALID"), Signal.score.desc()).first()
        sym = sg.symbol
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
    assert isinstance(d["queues"], dict)  # {} without a Redis broker (tests), per-queue depth otherwise


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


def test_live_setups_ignore_sample_calendar_news_and_fx(app_client):
    from app.core.db import SessionLocal
    from app.models import EconomicEvent
    from app.services.scan_service import enrich_setups

    class SampleFx:
        def convert(self, a, b):
            return {"rate": 83.0, "path": ["DEMO_USDINR"], "as_of": "2026-09-29", "is_sample": True}

    db = SessionLocal()
    try:
        db.add(EconomicEvent(provider="sample", external_id="t-fomc", event_time=datetime.now(timezone.utc) + timedelta(hours=3), country="US",
                             currency="USD", name="FOMC (sample)", impact="High", source="sample", is_sample=True))
        db.commit()

        def setup():
            return {"symbol": "LIVEX", "current_price": 100.0, "stop": 90.0, "targets": [115.0, 130.0], "direction": "LONG", "status": "VALID",
                    "checks": [], "explanation": {"risk_factors": []}}

        for is_sample in (False, True):
            st = setup()
            info = {"LIVEX": {"id": 1, "currency": "USD", "meta": {}, "is_index": False, "is_sample": is_sample}}
            enrich_setups(db, [st], "US", info, SampleFx(), {})
            names = [e["name"] for e in st["events"]["upcoming"]]
            if is_sample:  # sample instrument: sample context is fine (all labelled SAMPLE)
                assert st["inr"]["is_sample"] and "FOMC (sample)" in names
            else:  # real instrument: no fake FX rate, no fake events, and the gap is surfaced
                assert st["inr"]["available"] is False and "rate" not in st["inr"]
                assert "FOMC (sample)" not in names
                assert any(c["name"] == "Economic calendar" and c["severity"] == "warn" for c in st["checks"])
                assert st["status"] == "VALID"  # a missing calendar warns, it does not fabricate a block
    finally:
        db.query(EconomicEvent).filter_by(external_id="t-fomc").delete()
        db.commit()
        db.close()


def test_strategy_disabled_per_market_blocks_live_setups_only(app_client, admin_headers):
    from app.core.db import SessionLocal
    from app.services.scan_service import enrich_setups

    r = app_client.put("/api/v1/admin/strategy-controls/NSE/support_bounce", headers=admin_headers,
                       json={"enabled": False, "reason": "negative expectancy on point-in-time history"})
    assert r.status_code == 200 and "support_bounce" in r.json()["disabled"]
    assert app_client.put("/api/v1/admin/strategy-controls/NSE/nope", headers=admin_headers, json={"enabled": False}).status_code == 404
    assert app_client.put("/api/v1/admin/strategy-controls/MARS/support_bounce", headers=admin_headers, json={"enabled": False}).status_code == 404
    listed = {s["id"]: s for s in app_client.get("/api/v1/strategies", headers=admin_headers).json()["builtin"]}
    assert "NSE" in listed["support_bounce"]["disabled_markets"] and listed["breakout_volume"]["disabled_markets"] == {}
    db = SessionLocal()
    try:
        mk = lambda sid: {"symbol": "X", "strategy_id": sid, "strategy_name": sid, "current_price": 10.0, "stop": 9.0, "targets": [11.5, 13.0],  # noqa: E731
                          "direction": "LONG", "status": "VALID", "checks": [], "explanation": {"risk_factors": []}}
        a, b = mk("support_bounce"), mk("breakout_volume")
        enrich_setups(db, [a, b], "NSE", {"X": {"id": 1, "currency": "INR", "meta": {}, "is_index": False, "is_sample": True}}, None, {})
        assert a["status"] == "NO_TRADE" and any(c["name"] == "Strategy enabled" and "negative expectancy" in c["detail"] for c in a["checks"])
        assert b["status"] == "VALID"
    finally:
        db.close()
    audit = app_client.get("/api/v1/admin/audit-logs", headers=admin_headers).json()
    assert any(x["action"] == "strategy.disable" for x in audit.get("items", audit))
    assert app_client.put("/api/v1/admin/strategy-controls/NSE/support_bounce", headers=admin_headers, json={"enabled": True}).json()["disabled"] == {}


def test_sample_news_never_mentions_real_instruments(app_client):
    from app.core.db import SessionLocal
    from app.models import Instrument as InstrumentRow
    from app.models import NewsArticle, NewsSymbol
    from app.services.events_service import ingest_news

    db = SessionLocal()
    try:
        db.add(InstrumentRow(symbol="REALNEWSUSDT", name="real", asset_class="CRYPTO", exchange="BINANCE", currency="USD", market="LIVEMKT", is_sample=False))
        db.add(NewsArticle(provider="sample", external_id="old-fake", published_at=datetime.now(timezone.utc), title="[SAMPLE] fake",
                           summary="", source="Sample Wire", markets=["LIVEMKT"], sentiment_label="Neutral", sentiment_score=0.0,
                           sentiment_method="x", sentiment_terms=[], category="General", is_sample=True,
                           symbols=[NewsSymbol(symbol="REALNEWSUSDT")]))
        db.commit()
        out = ingest_news(db, "LIVEMKT")
        assert "skipped" in out and out["purged"] == 1
        assert db.query(NewsArticle).filter_by(external_id="old-fake").count() == 0
        # a sample market still gets (labelled) sample news
        nse = ingest_news(db, "NSE")
        assert nse.get("provider") == "sample"
    finally:
        db.close()
