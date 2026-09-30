"""Position sizing and portfolio risk."""
from __future__ import annotations

import math
from typing import Dict, List, Optional


def position_size(capital: float, risk_pct: float, entry: float, stop: float, lot_size: int = 1,
                  max_position_pct: Optional[float] = None) -> Dict:
    if capital <= 0 or risk_pct <= 0 or entry <= 0 or stop <= 0 or entry == stop:
        raise ValueError("capital, risk %, entry and stop must be positive and entry != stop")
    max_risk = capital * risk_pct / 100
    per_unit = abs(entry - stop)
    qty = math.floor(max_risk / per_unit / lot_size) * lot_size
    capped_by = None
    if max_position_pct:
        cap_qty = math.floor(capital * max_position_pct / 100 / entry / lot_size) * lot_size
        if cap_qty < qty:
            qty, capped_by = cap_qty, f"max position {max_position_pct:g}% of capital"
    cap_qty = math.floor(capital / entry / lot_size) * lot_size
    if cap_qty < qty:
        qty, capped_by = cap_qty, "available capital"
    return {
        "max_risk": round(max_risk, 2),
        "risk_per_unit": round(per_unit, 4),
        "quantity": int(qty),
        "position_value": round(qty * entry, 2),
        "max_loss": round(qty * per_unit, 2),
        "capital_used_pct": round(100 * qty * entry / capital, 2),
        "capped_by": capped_by,
        "direction": "LONG" if stop < entry else "SHORT",
    }


def atr_stop(entry: float, atr_value: float, mult: float = 2.0, direction: str = "LONG") -> float:
    return round(entry - mult * atr_value if direction == "LONG" else entry + mult * atr_value, 4)


def volatility_position_size(capital: float, risk_pct: float, entry: float, atr_value: float, atr_mult: float = 2.0,
                             direction: str = "LONG", lot_size: int = 1) -> Dict:
    stop = atr_stop(entry, atr_value, atr_mult, direction)
    out = position_size(capital, risk_pct, entry, stop, lot_size)
    out["stop"] = stop
    out["method"] = f"{atr_mult:g}×ATR stop"
    return out


def portfolio_risk(capital: float, positions: List[Dict]) -> Dict:
    """positions: [{symbol, quantity, entry, stop, sector?}] -> open risk if all stops hit."""
    total, by_sector = 0.0, {}
    for p in positions:
        r = abs(p["entry"] - p["stop"]) * p["quantity"]
        total += r
        by_sector[p.get("sector") or "Unknown"] = by_sector.get(p.get("sector") or "Unknown", 0.0) + r
    return {
        "open_risk": round(total, 2),
        "open_risk_pct": round(100 * total / capital, 2) if capital else None,
        "by_sector_pct": {k: round(100 * v / capital, 2) for k, v in by_sector.items()} if capital else {},
    }
