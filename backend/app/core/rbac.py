"""Roles and permissions. Seeded idempotently at startup / migration time."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

PERMISSIONS = {
    "market:read": "View market overview, regime, breadth, sectors",
    "signals:read": "View top setups (limited count)",
    "signals:read_all": "View every setup, NO TRADE list and full history",
    "analysis:read": "View instrument analysis and charts",
    "watchlists:write": "Create and edit own watchlists",
    "watchlists:unlimited": "More than 3 watchlists",
    "admin:users": "Manage users and roles",
    "admin:settings": "Configure scoring weights, labels and thresholds",
    "admin:providers": "Manage data providers and API keys",
    "admin:jobs": "Trigger and view background jobs",
    "audit:read": "Read audit logs",
    "options:read": "View NIFTY options market state, option chain, OI/IV analytics, payoff calculator",
    "options:signals": "View option setups and the options strategy engine",
    "portfolio:write": "Paper-trade and keep a trade journal",
    "portfolio:unlimited": "More than one paper portfolio",
    "alerts:write": "Create alerts and receive notifications",
    "alerts:unlimited": "More than the standard number of active alerts",
}

_STANDARD = ["market:read", "signals:read", "analysis:read", "watchlists:write", "options:read", "portfolio:write", "alerts:write"]
_PREMIUM = _STANDARD + ["signals:read_all", "watchlists:unlimited", "options:signals", "portfolio:unlimited", "alerts:unlimited"]
ROLES = {
    "standard": ("Standard user — basic analysis", _STANDARD),
    "premium": ("Premium user — advanced analysis and backtests", _PREMIUM),
    "analyst": ("Analyst — full access to setups", _PREMIUM),
    "admin": ("Administrator — full access", list(PERMISSIONS)),
}
DEFAULT_ROLE = "standard"


def seed_rbac(db: Session) -> None:
    from app.models import Permission, Role

    existing = {p.code: p for p in db.scalars(select(Permission))}
    for code, desc in PERMISSIONS.items():
        if code not in existing:
            existing[code] = Permission(code=code, description=desc)
            db.add(existing[code])
    db.flush()
    for name, (desc, perms) in ROLES.items():
        role = db.scalar(select(Role).where(Role.name == name))
        if role is None:
            role = Role(name=name, description=desc)
            db.add(role)
        role.permissions = [existing[p] for p in perms]
    db.commit()
