"""Event risk: economic releases, earnings and news context attached to setups.

* High-impact economic events that affect the setup's market/currencies:
  within `macro_block_hours` → BLOCK for macro-sensitive setups (forex, index/option
  underlyings, short-volatility options), WARN for single stocks and crypto;
  within `macro_warn_days` → WARN for everyone.
* Earnings history: the typical price reaction around past earnings dates,
  computed from stored bars, compared with an ordinary 2-day move.
* News: recent headline sentiment for the instrument. Context only — a WARN
  when recent news clearly opposes the setup direction; it never creates or blocks a trade.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .validation import Check

# which markets / currencies a country's releases move
COUNTRY_MARKETS = {
    "IN": {"markets": {"NSE", "NFO"}, "currencies": {"INR"}},
    "US": {"markets": {"US", "CRYPTO", "NSE", "NFO"}, "currencies": {"USD"}},  # Fed/NFP move global risk, incl. India
    "EU": {"markets": {"EUROPE"}, "currencies": {"EUR"}},
    "DE": {"markets": {"EUROPE"}, "currencies": {"EUR"}},
    "FR": {"markets": {"EUROPE"}, "currencies": {"EUR"}},
    "GB": {"markets": {"EUROPE"}, "currencies": {"GBP"}},
    "JP": {"markets": {"ASIA"}, "currencies": {"JPY"}},
    "CN": {"markets": {"ASIA"}, "currencies": {"CNY"}},
    "HK": {"markets": {"ASIA"}, "currencies": {"HKD"}},
    "SG": {"markets": {"ASIA"}, "currencies": {"SGD"}},
    "KR": {"markets": {"ASIA"}, "currencies": {"KRW"}},
    "CH": {"markets": set(), "currencies": {"CHF"}},
    "AU": {"markets": set(), "currencies": {"AUD"}},
    "CA": {"markets": set(), "currencies": {"CAD"}},
    "NZ": {"markets": set(), "currencies": {"NZD"}},
}


@dataclass
class EventRiskConfig:
    macro_block_hours: float = 24.0
    macro_warn_days: float = 3.0
    news_window_days: int = 7
    news_warn_min_articles: int = 2


def relevant_events(events: List[dict], market: str, currencies: Optional[set] = None) -> List[dict]:
    out = []
    for e in events:
        m = COUNTRY_MARKETS.get((e.get("country") or "").upper(), {"markets": set(), "currencies": set()})
        ccy = (e.get("currency") or "").upper()
        hit_market = market in m["markets"]
        hit_ccy = bool(currencies) and (ccy in currencies or bool(m["currencies"] & currencies))
        if hit_market or hit_ccy:
            out.append(e)
    return out


def macro_checks(events: List[dict], now: datetime, macro_sensitive: bool, cfg: EventRiskConfig) -> List[Check]:
    checks = []
    for e in sorted(events, key=lambda x: x["event_time"]):
        if e.get("impact") != "High":
            continue
        t = pd.Timestamp(e["event_time"])
        t = t.tz_localize("UTC") if t.tzinfo is None else t
        hours = (t - pd.Timestamp(now)).total_seconds() / 3600
        if hours < 0 or hours > cfg.macro_warn_days * 24:
            continue
        when = f"in {hours:.0f}h" if hours < 48 else f"in {hours / 24:.1f} days"
        detail = f"⚠ {e['name']} ({e.get('country')}) {when} — high-impact event"
        if hours <= cfg.macro_block_hours:
            checks.append(Check("Event risk (macro)", False, "block" if macro_sensitive else "warn", detail))
        else:
            checks.append(Check("Macro event approaching", False, "warn", detail))
    return checks


def upcoming_summary(events: List[dict], now: datetime, days: int = 7, limit: int = 8) -> List[dict]:
    out = []  # hours_until is a snapshot at `hours_until_as_of`; clients should recompute from event_time
    for e in sorted(events, key=lambda x: x["event_time"]):
        t = pd.Timestamp(e["event_time"])
        t = t.tz_localize("UTC") if t.tzinfo is None else t
        h = (t - pd.Timestamp(now)).total_seconds() / 3600
        if 0 <= h <= days * 24:
            out.append({"name": e["name"], "country": e.get("country"), "impact": e.get("impact"), "event_time": t.isoformat(),
                        "hours_until": round(h, 1), "hours_until_as_of": pd.Timestamp(now).isoformat(), "forecast": e.get("forecast"), "previous": e.get("previous"), "source": e.get("source")})
    return out[:limit]


def earnings_reaction(close: pd.Series, dates: List, window: int = 1) -> Dict:
    """Absolute close-to-close move spanning each earnings date (prior close → next close),
    compared with the stock's ordinary absolute 2-day move."""
    close = close.dropna().sort_index()
    if len(close) < 60 or not dates:
        return {"count": 0}
    moves = []
    idx = close.index
    for d in dates:
        pos = idx.searchsorted(pd.Timestamp(d))
        if pos - window < 0 or pos + window >= len(idx):
            continue
        moves.append(float(close.iloc[pos + window] / close.iloc[pos - window] - 1))
    if not moves:
        return {"count": 0}
    base = float((close.pct_change(2 * window).abs()).median())
    avg = float(np.mean(np.abs(moves)))
    return {"count": len(moves), "avg_abs_move_pct": round(100 * avg, 2), "max_abs_move_pct": round(100 * float(np.max(np.abs(moves))), 2),
            "up_moves": int(sum(m > 0 for m in moves)), "typical_2d_move_pct": round(100 * base, 2),
            "vs_typical": round(avg / base, 2) if base > 0 else None,
            "note": "Close-to-close move from the session before to the session after each report."}


def news_context(articles: List[dict], direction: Optional[str], now: datetime, cfg: EventRiskConfig) -> Dict:
    now_ts = pd.Timestamp(now)
    recent = []
    for a in articles:
        t = pd.Timestamp(a["published_at"])
        t = t.tz_localize("UTC") if t.tzinfo is None else t
        age_h = (now_ts - t).total_seconds() / 3600
        if 0 <= age_h <= cfg.news_window_days * 24:
            recent.append({**a, "_age_h": age_h})
    counts = {k: sum(1 for a in recent if a["sentiment_label"] == k) for k in ("Positive", "Neutral", "Negative")}
    net = float(np.mean([a["sentiment_score"] for a in recent])) if recent else 0.0
    checks: List[Check] = []
    last3 = [a for a in recent if a["_age_h"] <= 72]
    neg3 = sum(a["sentiment_label"] == "Negative" for a in last3)
    pos3 = sum(a["sentiment_label"] == "Positive" for a in last3)
    if direction == "LONG" and neg3 >= cfg.news_warn_min_articles and neg3 > pos3:
        checks.append(Check("News flow", False, "warn", f"{neg3} negative headline(s) in the last 3 days oppose a long setup"))
    if direction == "SHORT" and pos3 >= cfg.news_warn_min_articles and pos3 > neg3:
        checks.append(Check("News flow", False, "warn", f"{pos3} positive headline(s) in the last 3 days oppose a short setup"))
    recent.sort(key=lambda a: a["_age_h"])
    return {
        "window_days": cfg.news_window_days, "articles": len(recent), "counts": counts, "net_score": round(net, 3),
        "latest": [{k: a.get(k) for k in ("id", "title", "source", "published_at", "url", "sentiment_label", "category", "is_sample")} for a in recent[:3]],
        "note": "News sentiment is context only; it never generates or scores a setup.",
        "checks": [c.to_dict() for c in checks],
    }


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
