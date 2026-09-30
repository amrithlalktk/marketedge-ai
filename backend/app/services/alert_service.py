"""Alert evaluation and triggering (idempotent per bar; cooldown-protected)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Alert, Instrument
from app.services.market_data import load_bars
from app.services.notify_service import notify
from engine.alerts import BAR_KINDS, KINDS, evaluate, matches_setup, unusual_options
from engine.features import build_features


def _cooling(a: Alert, now: datetime) -> bool:
    last = a.last_triggered_at
    if last is None:
        return False
    last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
    return now - last < timedelta(minutes=a.cooldown_minutes)


def _fire(db: Session, a: Alert, message: str, payload: dict, now: datetime) -> None:
    title = f"{a.symbol or 'Market'}: {KINDS[a.kind].split(' (')[0]}"
    link = f"/stocks/{a.symbol}" if a.symbol else ("/setups" if a.kind == "new_setup" else "/options")
    notify(db, a.user_id, title, message + (f" — {a.note}" if a.note else ""), link=link, payload={"alert_id": a.id, **payload},
           channels=a.channels or None, alert_id=a.id)
    a.last_triggered_at, a.trigger_count = now, a.trigger_count + 1
    if not a.repeat:
        a.status = "triggered"


def _expire(db: Session, now: datetime) -> None:
    for a in db.scalars(select(Alert).where(Alert.status == "active", Alert.expires_at.isnot(None))):
        exp = a.expires_at if a.expires_at.tzinfo else a.expires_at.replace(tzinfo=timezone.utc)
        if exp < now:
            a.status = "expired"


def evaluate_bar_alerts(db: Session, market: Optional[str] = None) -> Dict[str, int]:
    now = datetime.now(timezone.utc)
    _expire(db, now)
    q = select(Alert).where(Alert.status == "active", Alert.kind.in_(list(BAR_KINDS)), Alert.instrument_id.isnot(None))
    alerts = list(db.scalars(q))
    if market:
        mids = {i for (i,) in db.execute(select(Instrument.id).where(Instrument.market == market))}
        alerts = [a for a in alerts if a.instrument_id in mids]
    by_ins: Dict[int, List[Alert]] = {}
    for a in alerts:
        by_ins.setdefault(a.instrument_id, []).append(a)
    fired = checked = 0
    for iid, group in by_ins.items():
        sym = group[0].symbol
        bars = load_bars(db, {iid: sym}).get(sym)
        if bars is None or len(bars) < 60:
            continue
        f = build_features(bars)
        bar = str(f.index[-1])
        for a in group:
            if a.last_bar == bar:  # this bar was already evaluated for this alert
                continue
            a.last_bar = bar
            checked += 1
            hit, msg, vals = evaluate(a.kind, a.params or {}, f)
            if hit and not _cooling(a, now):
                _fire(db, a, f"{sym} {msg} (bar {vals.get('bar')})", {"values": vals}, now)
                fired += 1
    db.commit()
    return {"checked": checked, "fired": fired}


def new_setup_alerts(db: Session, market: str, valid_setups: List[dict]) -> int:
    now = datetime.now(timezone.utc)
    fired = 0
    for a in db.scalars(select(Alert).where(Alert.status == "active", Alert.kind == "new_setup")):
        if a.params.get("market") and a.params["market"] != market:
            continue
        hits = [s for s in valid_setups if matches_setup(a.params or {}, s)]
        if hits and not _cooling(a, now):
            lines = "; ".join(f"{s['symbol']} {s['direction']} {s['strategy']['name']} (score {s['score']:.0f})" for s in hits[:5])
            _fire(db, a, f"{len(hits)} new VALID setup(s) in {market}: {lines}", {"market": market, "symbols": [s["symbol"] for s in hits]}, now)
            fired += 1
    db.commit()
    return fired


def options_alerts(db: Session, chain_rows: list) -> int:
    now = datetime.now(timezone.utc)
    fired = 0
    for a in db.scalars(select(Alert).where(Alert.status == "active", Alert.kind == "unusual_options")):
        hits = unusual_options(chain_rows, float((a.params or {}).get("multiple", 3.0)))
        if hits and not _cooling(a, now):
            desc = "; ".join(f"{int(h['strike'])} {h['option_type']} ΔOI {h['oi_change']:+,.0f} ({h['multiple_of_median']}× median)" for h in hits)
            _fire(db, a, f"Unusual NIFTY options activity: {desc}", {"strikes": hits}, now)
            fired += 1
    db.commit()
    return fired


def alerts_from_setup(db: Session, user_id: int, setup: dict, signal_id: int, kinds: List[str], channels: List[str]) -> List[Alert]:
    ins = db.scalar(select(Instrument).where(Instrument.symbol == setup["symbol"]))
    made = []
    for k in kinds:
        if k == "entry":
            params, kind = {"low": setup["entry_zone"][0], "high": setup["entry_zone"][1]}, "entry_reached"
        elif k in ("target1", "target2"):
            params, kind = {"level": setup["targets"][0 if k == "target1" else 1], "direction": setup["direction"]}, "target_reached"
        elif k == "stop":
            params, kind = {"level": setup["stop"], "direction": setup["direction"]}, "stop_reached"
        else:
            raise ValueError(f"Unknown setup alert {k!r}")
        a = Alert(user_id=user_id, instrument_id=ins.id, symbol=ins.symbol, kind=kind, params=params, channels=channels, signal_id=signal_id,
                  note=f"{setup['strategy']['name']} {setup['direction']} ({k})", expires_at=datetime.now(timezone.utc) + timedelta(days=30))
        db.add(a)
        made.append(a)
    db.commit()
    return made
