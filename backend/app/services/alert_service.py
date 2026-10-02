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


# ---------------------------------------------------------------- daily trade ideas digest
def _money(v) -> str:
    return "–" if v is None else (f"{v:,.2f}" if abs(v) < 1000 else f"{v:,.1f}")


def _chance(p: dict) -> str:
    if not p or p.get("t1_hit_rate") is None or not p.get("sample_size"):
        return "chance: not enough history"
    return f"{p['t1_hit_rate']:.0f}% reach target 1 ({p['sample_size']} past cases)"


def daily_ideas_text(db: Session) -> Optional[dict]:
    """Today's VALID NIFTY option and NSE stock setups as plain lines (or 'No trade today' with the reasons)."""
    from app.models import Signal
    from app.services.scan_service import latest_run

    nse, nfo = latest_run(db, "NSE"), latest_run(db, "NFO")
    if nse is None:
        return None
    lines = []
    if nfo is not None:
        for sg in db.scalars(select(Signal).where(Signal.scan_run_id == nfo.id, Signal.status == "VALID").order_by(Signal.score.desc()).limit(3)):
            o = sg.payload or {}
            c, t = o.get("contract") or {}, o.get("targets") or [None, None]
            lines.append(f"{c.get('label', sg.symbol)}: entry ₹{_money(o.get('entry'))}, stop loss ₹{_money(o.get('stop'))}, "
                         f"exit ₹{_money(t[0])} / ₹{_money(t[1])} · {_chance(o.get('probability') or {})}")
    for sg in db.scalars(select(Signal).where(Signal.scan_run_id == nse.id, Signal.status == "VALID").order_by(Signal.score.desc()).limit(5)):
        x = sg.payload or {}
        lo, hi = (x.get("entry_zone") or [None, None])[:2]
        t = x.get("targets") or [None, None]
        entry = _money(lo) if lo == hi else f"{_money(lo)}–{_money(hi)}"
        lines.append(f"{sg.symbol} {sg.direction}: entry {entry}, stop loss {_money(x.get('stop'))}, "
                     f"exit {_money(t[0])} / {_money(t[1])} · {_chance(x.get('probability') or {})}")
    as_of = str(nse.as_of)
    if lines:
        title = f"Trade ideas from the {as_of} close: {len(lines)}"
        body = "\n".join(lines) + "\nChances are historical frequencies, not guarantees. Always place the stop loss."
    else:
        why = [m for m in ((nfo.stats or {}).get("market_message") if nfo else None, (nse.stats or {}).get("market_message")) if m]
        title = f"No trade today ({as_of} close)"
        body = "No NIFTY option or stock setup passed every safety check. " + " ".join(why)
    return {"as_of": as_of, "title": title, "body": body, "ideas": len(lines)}


def daily_ideas_alerts(db: Session) -> int:
    """Send the digest once per session to every active 'daily_ideas' alert (idempotent via last_bar = session date)."""
    digest = daily_ideas_text(db)
    if digest is None:
        return 0
    now, sent = datetime.now(timezone.utc), 0
    for a in db.scalars(select(Alert).where(Alert.status == "active", Alert.kind == "daily_ideas")):
        if a.last_bar == digest["as_of"]:
            continue
        notify(db, a.user_id, digest["title"], digest["body"] + (f" — {a.note}" if a.note else ""), link="/",
               payload={"alert_id": a.id, "as_of": digest["as_of"], "ideas": digest["ideas"]}, channels=a.channels or None, alert_id=a.id)
        a.last_bar, a.last_triggered_at, a.trigger_count = digest["as_of"], now, a.trigger_count + 1
        sent += 1
    db.commit()
    return sent
