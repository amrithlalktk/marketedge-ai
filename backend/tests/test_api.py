from __future__ import annotations

import pyotp

from tests.conftest import login, make_user

API = "/api/v1"


# ------------------------------------------------------------------ auth
def test_register_rejects_weak_password(app_client):
    r = app_client.post(f"{API}/auth/register", json={"email": "weak@example.com", "password": "aaaaaaaaaaaa"})
    assert r.status_code == 422


def test_login_me_and_refresh_rotation(app_client):
    app_client.post(f"{API}/auth/register", json={"email": "rot@example.com", "password": "Str0ng!Passw0rd"})
    r = app_client.post(f"{API}/auth/login", json={"email": "rot@example.com", "password": "Str0ng!Passw0rd"})
    assert r.status_code == 200
    old_cookie = r.cookies.get("me_refresh")
    me = app_client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()
    assert me["role"] == "standard" and "signals:read" in me["permissions"] and "backtests:run" not in me["permissions"]
    r2 = app_client.post(f"{API}/auth/refresh")
    assert r2.status_code == 200 and r2.cookies.get("me_refresh") != old_cookie
    # replaying the rotated token revokes the whole session family
    new_cookie = r2.cookies.get("me_refresh")
    app_client.cookies.clear()
    # immediate replay (two tabs racing) -> 409, family NOT revoked
    assert app_client.post(f"{API}/auth/refresh", headers={"Cookie": f"me_refresh={old_cookie}"}).status_code == 409
    # replay outside the grace window -> treated as theft: whole family revoked
    from datetime import datetime, timedelta, timezone
    from app.core.db import SessionLocal
    from app.core.security import token_hash
    from app.models import UserSession
    db = SessionLocal()
    sess = db.query(UserSession).filter(UserSession.token_hash == token_hash(old_cookie)).one()
    sess.revoked_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db.commit()
    db.close()
    assert app_client.post(f"{API}/auth/refresh", headers={"Cookie": f"me_refresh={old_cookie}"}).status_code == 401
    assert app_client.post(f"{API}/auth/refresh", headers={"Cookie": f"me_refresh={new_cookie}"}).status_code == 401
    app_client.cookies.clear()


def test_bad_password_and_lockout(app_client):
    app_client.post(f"{API}/auth/register", json={"email": "lock@example.com", "password": "Str0ng!Passw0rd"})
    for _ in range(5):
        assert app_client.post(f"{API}/auth/login", json={"email": "lock@example.com", "password": "wrong"}).status_code == 401
    assert app_client.post(f"{API}/auth/login", json={"email": "lock@example.com", "password": "Str0ng!Passw0rd"}).status_code == 423


def test_two_factor_flow(app_client):
    h = make_user(app_client, "tfa@example.com")
    secret = app_client.post(f"{API}/auth/2fa/setup", headers=h).json()["secret"]
    assert app_client.post(f"{API}/auth/2fa/enable", json={"code": "000000"}, headers=h).status_code == 400
    assert app_client.post(f"{API}/auth/2fa/enable", json={"code": pyotp.TOTP(secret).now()}, headers=h).status_code == 200
    r = app_client.post(f"{API}/auth/login", json={"email": "tfa@example.com", "password": "Str0ng!Passw0rd"})
    assert r.status_code == 401 and r.headers.get("X-2FA-Required") == "1"
    login(app_client, "tfa@example.com", "Str0ng!Passw0rd", totp=pyotp.TOTP(secret).now())


def test_unauthenticated_and_rbac(app_client, admin_headers):
    assert app_client.get(f"{API}/markets/overview").status_code == 401
    h = make_user(app_client, "std@example.com")
    assert app_client.get(f"{API}/admin/users", headers=h).status_code == 403
    assert app_client.post(f"{API}/admin/jobs/scan", headers=h).status_code == 403
    assert app_client.get(f"{API}/admin/users", headers=admin_headers).status_code == 200


# ------------------------------------------------------------------ market data & signals (SAMPLE)
def test_overview_is_labelled_sample(app_client, admin_headers, scanned):
    ov = app_client.get(f"{API}/markets/overview", headers=admin_headers).json()
    assert ov["is_sample"] is True
    assert all(i["symbol"].startswith("DEMO_") and i["data"]["is_sample"] for i in ov["indices"])
    assert ov["regime"]["regime"] in {"Strong Bull", "Weak Bull", "Range", "Bear", "Panic/Selloff", "Recovery"}
    assert "disclaimer" in ov and "do not guarantee" in ov["disclaimer"]
    b = app_client.get(f"{API}/markets/breadth", headers=admin_headers).json()
    assert 0 <= b["pct_above_50dma"] <= 100
    secs = app_client.get(f"{API}/markets/sectors", headers=admin_headers).json()
    assert secs["sectors"] and secs["sectors"][0]["rank"] == 1


def test_signals_top_contract(app_client, admin_headers, scanned):
    r = app_client.get(f"{API}/signals/top?limit=50", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert "disclaimer" in body and "sort_note" in body
    for s in body["items"]:
        assert s["status"] == "VALID"
        assert s["probability"]["sample_size"] >= 30  # never a probability without its sample size
        assert s["probability"]["backtest_period"]
        assert s["data"]["is_sample"] is True
        assert s["rr_t2"] >= 2.0
    nt = app_client.get(f"{API}/signals?status=NO_TRADE", headers=admin_headers).json()
    for s in nt["items"]:
        assert s["blocking_checks"], "NO_TRADE must state why"


def test_standard_user_top_limit(app_client, scanned):
    h = make_user(app_client, "std2@example.com")
    body = app_client.get(f"{API}/signals/top?limit=50", headers=h).json()
    assert len(body["items"]) <= 5
    assert app_client.get(f"{API}/signals?status=NO_TRADE", headers=h).status_code == 403


def test_stock_endpoints(app_client, admin_headers, scanned):
    lst = app_client.get(f"{API}/stocks?q=DEMO_00", headers=admin_headers).json()
    assert lst["total"] >= 1
    sym = lst["items"][0]["symbol"]
    d = app_client.get(f"{API}/stocks/{sym}", headers=admin_headers).json()
    assert d["quote"]["price"] > 0 and d["fundamentals_available"] is False
    c = app_client.get(f"{API}/stocks/{sym}/candles?limit=100", headers=admin_headers).json()
    assert len(c["bars"]["t"]) == 100 and len(c["bars"]["ema20"]) == 100
    a = app_client.get(f"{API}/stocks/{sym}/analysis?mode=technical", headers=admin_headers).json()
    assert a["status"] in ("VALID", "NO_TRADE", "NO_SETUP") and a["data"]["is_sample"] is True
    assert "disclaimer" in a
    assert app_client.get(f"{API}/stocks/NOPE/analysis", headers=admin_headers).status_code == 404


def test_strategies_have_performance_panel(app_client, admin_headers, scanned):
    s = app_client.get(f"{API}/admin/strategies?market=NSE", headers=admin_headers).json()
    bo = next(x for x in s["items"] if x["id"] == "breakout_volume")
    assert bo["performance"]["trades"] > 0 and bo["performance"]["backtest_period"]
    assert set(bo["performance"]["segments"]) == {"training", "validation", "out_of_sample"}


# ------------------------------------------------------------------ watchlists
def test_watchlist_crud_and_limits(app_client, scanned):
    h = make_user(app_client, "wl@example.com")
    wl = app_client.post(f"{API}/watchlists", json={"name": "Breakouts"}, headers=h).json()
    r = app_client.post(f"{API}/watchlists/{wl['id']}/items", json={"symbol": "DEMO_001", "tags": ["High conviction", " "]}, headers=h)
    assert r.status_code == 201 and r.json()["items"][0]["tags"] == ["High conviction"]
    assert app_client.post(f"{API}/watchlists/{wl['id']}/items", json={"symbol": "DEMO_001"}, headers=h).status_code == 409
    item = r.json()["items"][0]
    assert app_client.patch(f"{API}/watchlists/{wl['id']}/items/{item['id']}", json={"tags": ["Monitor"]}, headers=h).json()["items"][0]["tags"] == ["Monitor"]
    for n in ("b", "c"):
        app_client.post(f"{API}/watchlists", json={"name": n}, headers=h)
    assert app_client.post(f"{API}/watchlists", json={"name": "d"}, headers=h).status_code == 403
    other = make_user(app_client, "wl2@example.com")
    assert app_client.delete(f"{API}/watchlists/{wl['id']}", headers=other).status_code == 404  # cannot touch others' lists
    assert app_client.delete(f"{API}/watchlists/{wl['id']}", headers=h).status_code == 204


# ------------------------------------------------------------------ risk
def test_risk_calculator(app_client, admin_headers):
    r = app_client.post(f"{API}/risk/position-size", json={"capital": 500000, "risk_pct": 1, "entry": 1000, "stop": 950}, headers=admin_headers).json()
    assert r["quantity"] == 100 and r["max_loss"] == 5000
    r = app_client.post(f"{API}/risk/position-size", json={"capital": 500000, "risk_pct": 1, "entry": 1000}, headers=admin_headers)
    assert r.status_code == 422


# ------------------------------------------------------------------ backtests


# ------------------------------------------------------------------ admin settings
def test_admin_can_configure_weights_and_labels(app_client, admin_headers):
    r = app_client.put(f"{API}/admin/settings/engine", json={"weights": {"trend": 25}, "labels": [[85, "A"], [70, "B"], [0, "C"]]}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["effective"]["weights"]["trend"] == 25
    assert app_client.put(f"{API}/admin/settings/engine", json={"weights": {"bogus": 1}}, headers=admin_headers).status_code == 422
    logs = app_client.get(f"{API}/admin/audit-logs?action=settings.engine_update", headers=admin_headers).json()["items"]
    assert logs
    app_client.put(f"{API}/admin/settings/engine", json={"weights": {"trend": 20}, "labels": [[90, "Very strong setup"], [75, "Strong setup"], [60, "Moderate setup"], [0, "Do not recommend"]]}, headers=admin_headers)


def test_provider_keys_never_returned(app_client, admin_headers):
    app_client.post(f"{API}/admin/providers/keys", json={"provider": "vendor_x", "name": "API_KEY", "value": "super-secret"}, headers=admin_headers)
    body = app_client.get(f"{API}/admin/providers", headers=admin_headers).text
    assert "super-secret" not in body and "vendor_x" in body


def test_security_headers(app_client):
    r = app_client.get("/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"


def test_rate_limit_uses_forwarded_ip_only_from_trusted_proxy():
    from starlette.requests import Request
    from app.core.middleware import client_ip
    def req(peer, xff=None):
        headers = [(b"x-forwarded-for", xff.encode())] if xff else []
        return Request({"type": "http", "client": (peer, 1), "headers": headers})
    assert client_ip(req("127.0.0.1", "203.0.113.9")) == "203.0.113.9"
    assert client_ip(req("198.51.100.7", "203.0.113.9")) == "198.51.100.7"  # spoofed XFF from the internet ignored
    assert client_ip(req("172.18.0.5", "1.2.3.4, 203.0.113.9")) == "203.0.113.9"
