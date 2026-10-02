"""Point-in-time universe membership for markets whose scan universe is "top N by traded value"
(CRYPTO on Binance). Shared by the scan, backtests and ML so all three see the same history."""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import pandas as pd
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.markets import market_config
from engine.universe import membership_summary, top_n_membership


def pit_top_n(market: str) -> Optional[int]:
    s = get_settings()
    if market == "CRYPTO" and market_config(market).provider == "binance" and s.binance_pit_universe:
        return s.binance_universe_size
    if market == "NSE" and market_config(market).provider == "upstox":
        return s.upstox_universe_size or 500  # top N of the stored pool by traded value, per date
    return None


def membership_for(db: Session, market: str, info: Dict[str, dict], bars: Dict[str, pd.DataFrame]
                   ) -> Tuple[Optional[Dict[str, pd.Series]], set, dict]:
    """(membership, delisted symbols, summary) — (None, set(), {}) when the market has no top-N universe.
    Ranking always uses the WHOLE pool, even when `bars` holds only a backtest's chosen symbols."""
    n = pit_top_n(market)
    if not n:
        return None, set(), {}
    pool = [k for k, v in info.items() if not v["is_index"]]
    missing = {info[k]["id"]: k for k in pool if k not in bars}
    if missing:
        from app.services.market_data import load_bars

        bars = {**bars, **load_bars(db, missing)}
    membership = top_n_membership({k: bars[k] for k in pool if k in bars}, n)
    delisted = {k for k in pool if info[k].get("delisted_on")}
    summary = {"top_n": n, **membership_summary(membership, delisted)}
    if not delisted:
        summary["caveat"] = ("The provider lists only currently listed instruments: stocks delisted in the past are missing, "
                             "so some survivorship bias remains in the history (results are likely optimistic).")
    return membership, delisted, summary
