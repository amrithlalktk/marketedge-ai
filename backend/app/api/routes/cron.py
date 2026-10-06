"""Vercel Cron → start the daily GitHub Actions run on time.

GitHub delays scheduled workflows by hours under load; a workflow_dispatch starts right away. Vercel Cron calls this
endpoint at the scheduled hour with `Authorization: Bearer <CRON_SECRET>`; it only dispatches the workflow."""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, HTTPException, Query

from app.core.config import get_settings

router = APIRouter(prefix="/cron", tags=["cron"])


@router.get("/daily")
def daily(market: str = Query(..., pattern="^(NSE|CRYPTO)$"), authorization: str = Header("")):
    from app import jobs

    secret = get_settings().cron_secret
    if not secret:
        raise HTTPException(503, "CRON_SECRET is not set")
    if not hmac.compare_digest(authorization.encode(), f"Bearer {secret}".encode()):
        raise HTTPException(401, "Not allowed")
    try:
        return jobs.dispatch_github(market)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from None
