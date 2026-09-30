"""Currency conversion from the FX market's own stored bars (never a hard-coded rate).

Rates come from the latest close of FX instruments whose metadata carries
base/quote. Conversion tries direct, inverse, then a USD bridge, and always
returns the pairs used, their timestamp and whether they are SAMPLE data.
"""
from __future__ import annotations

from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DataStatus, Instrument, MarketBar


class FxBook:
    def __init__(self, quotes: Dict[tuple, dict]):
        self.quotes = quotes  # (base, quote) -> {rate, symbol, as_of, is_sample}

    def _direct(self, a: str, b: str) -> Optional[dict]:
        if (a, b) in self.quotes:
            q = self.quotes[(a, b)]
            return {"rate": q["rate"], "path": [q["symbol"]], "as_of": q["as_of"], "is_sample": q["is_sample"]}
        if (b, a) in self.quotes:
            q = self.quotes[(b, a)]
            return {"rate": 1 / q["rate"], "path": [f"1/{q['symbol']}"], "as_of": q["as_of"], "is_sample": q["is_sample"]}
        return None

    def convert(self, a: str, b: str) -> Optional[dict]:
        a, b = a.upper(), b.upper()
        if a == "USDT":
            a = "USD"
        if a == b:
            return {"rate": 1.0, "path": [], "as_of": None, "is_sample": False}
        d = self._direct(a, b)
        if d:
            return d
        x, y = self._direct(a, "USD"), self._direct("USD", b)
        if x and y:
            return {"rate": x["rate"] * y["rate"], "path": x["path"] + y["path"], "as_of": min(x["as_of"], y["as_of"]),
                    "is_sample": x["is_sample"] or y["is_sample"]}
        return None


def load_fx_book(db: Session) -> FxBook:
    quotes = {}
    fx = db.scalars(select(Instrument).where(Instrument.market == "FX", Instrument.is_active.is_(True))).all()
    for ins in fx:
        base, quote = (ins.meta or {}).get("base"), (ins.meta or {}).get("quote")
        if not base or not quote:
            continue
        bar = db.scalar(select(MarketBar).where(MarketBar.instrument_id == ins.id, MarketBar.interval == "1d").order_by(MarketBar.ts.desc()).limit(1))
        if bar is None:
            continue
        st = db.get(DataStatus, (ins.id, "1d"))
        quotes[(base, quote)] = {"rate": float(bar.close), "symbol": ins.symbol, "as_of": str(bar.ts.date()), "is_sample": bool(st and st.is_sample)}
    return FxBook(quotes)


def rate_series(db: Session, from_ccy: str, to_ccy: str):
    """Daily historical conversion series from_ccy→to_ccy from stored FX closes (direct, inverse or via USD).
    Returns (pd.Series, path) or (None, None)."""

    from app.services.market_data import load_bars

    a, b = ("USD" if from_ccy.upper() == "USDT" else from_ccy.upper()), to_ccy.upper()
    if a == b:
        return None, []
    pairs = {}
    for ins in db.scalars(select(Instrument).where(Instrument.market == "FX", Instrument.is_active.is_(True))):
        base, quote = (ins.meta or {}).get("base"), (ins.meta or {}).get("quote")
        if base and quote:
            pairs[(base, quote)] = ins

    def leg(x, y):
        if (x, y) in pairs:
            ins = pairs[(x, y)]
            s = load_bars(db, {ins.id: ins.symbol}).get(ins.symbol)
            return (s["close"], [ins.symbol]) if s is not None else (None, None)
        if (y, x) in pairs:
            ins = pairs[(y, x)]
            s = load_bars(db, {ins.id: ins.symbol}).get(ins.symbol)
            return ((1 / s["close"]), [f"1/{ins.symbol}"]) if s is not None else (None, None)
        return None, None

    s, p = leg(a, b)
    if s is not None:
        return s, p
    s1, p1 = leg(a, "USD")
    s2, p2 = leg("USD", b)
    if s1 is not None and s2 is not None:
        idx = s1.index.union(s2.index)
        return (s1.reindex(idx).ffill() * s2.reindex(idx).ffill()).dropna(), p1 + p2
    return None, None
