from __future__ import annotations

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="marketedge-test-")
os.environ.update({
    "ENVIRONMENT": "test",
    "DATABASE_URL": f"sqlite:///{_tmp}/test.db",
    "CELERY_ALWAYS_EAGER": "true",
    "SAMPLE_UNIVERSE_SIZE": "24",
    "MARKET_DATA_PROVIDER": "sample",
    "RATE_LIMIT_PER_MINUTE": "100000",
    "AUTH_RATE_LIMIT_PER_MINUTE": "100000",
    "SECRET_KEY": "test-secret-key-that-is-long-enough-123456",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.rbac import seed_rbac  # noqa: E402
import app.models  # noqa: E402,F401  (registers tables on Base.metadata)

ADMIN = ("admin@example.com", "Adm1n!Password")


@pytest.fixture(scope="session")
def app_client():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    seed_rbac(db)
    from app.cli import ensure_admin

    ensure_admin(db, *ADMIN)
    db.close()
    from app.main import app

    with TestClient(app) as c:
        yield c


def login(client, email, password, totp=None):
    body = {"email": email, "password": password}
    if totp:
        body["totp_code"] = totp
    r = client.post("/api/v1/auth/login", json=body)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def admin_headers(app_client):
    return login(app_client, *ADMIN)


@pytest.fixture(scope="session")
def scanned(app_client, admin_headers):
    """Ingest SAMPLE data and run one scan (eager Celery) for the whole session."""
    r = app_client.post("/api/v1/admin/jobs/ingest?full=true", headers=admin_headers)
    assert r.status_code == 202
    r = app_client.post("/api/v1/admin/jobs/scan", headers=admin_headers)
    assert r.status_code == 202
    job = app_client.get(f"/api/v1/admin/jobs/{r.json()['job_id']}", headers=admin_headers).json()
    assert job["status"] == "done", job
    return job


def make_user(client, email, role=None, admin_headers=None, password="Str0ng!Passw0rd"):
    r = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    if role:
        uid = r.json()["id"]
        assert client.patch(f"/api/v1/admin/users/{uid}", json={"role": role}, headers=admin_headers).status_code == 200
    return login(client, email, password)
