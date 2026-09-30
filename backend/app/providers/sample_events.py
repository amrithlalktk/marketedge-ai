"""SAMPLE / SYNTHETIC news, earnings and economic calendar — DEVELOPMENT AND TESTS ONLY.

Headlines are generated from templates about fictional DEMO_ instruments and are
prefixed "[SAMPLE]". Economic events use recurring real-world release NAMES with
synthetic dates and values, each marked "(sample)". Nothing here is real news.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta, timezone
from typing import List

import numpy as np

from .base import EarningsCalendarProvider, EconomicCalendarProvider, NewsProvider

SOURCE = "sample-synthetic"
_POS = ["{n} beats estimates as quarterly profit jumps", "{n} bags order worth crores from a large customer", "Brokerage upgrades {n}, raises target price",
        "{n} announces buyback at a premium", "{n} raises guidance on strong demand", "{n} wins contract for new expansion project"]
_NEG = ["{n} misses estimates; margins under pressure", "Regulator opens probe into {n} disclosures", "{n} cuts guidance citing weak demand",
        "Brokerage downgrades {n} to underperform", "{n} shares tumble after promoter stake sale", "{n} faces lawsuit over product recall"]
_NEU = ["{n} schedules board meeting to consider results", "{n} to present at investor conference", "{n} appoints new independent director",
        "{n} completes routine debt refinancing"]
_MACRO = [("Markets steady ahead of central bank decision", "macro"), ("Oil prices rise on supply concerns", "geopolitical"),
          ("Inflation data due this week; traders cautious", "macro"), ("New tariff proposals weigh on exporters", "geopolitical")]


def _rng(key: str):
    return np.random.default_rng(int(hashlib.sha256(key.encode()).hexdigest()[:8], 16))


class SampleNewsProvider(NewsProvider):
    is_sample = True
    name = "sample"

    def get_news(self, symbols: List[str], since: datetime, market: str) -> List[dict]:
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        out = []
        for sym in symbols:
            rs = _rng(f"news{sym}{now.date()}")
            for k in range(int(rs.integers(0, 4))):
                bucket = rs.choice(["pos", "neg", "neu"], p=[0.35, 0.3, 0.35])
                tpl = (_POS if bucket == "pos" else _NEG if bucket == "neg" else _NEU)[int(rs.integers(0, 6 if bucket != "neu" else 4))]
                ts = now - timedelta(hours=int(rs.integers(1, 14 * 24)))
                if ts < since:
                    continue
                name = sym.replace("DEMO_", "Demo ")
                out.append({"external_id": f"{sym}-{ts.isoformat()}-{k}", "published_at": ts.isoformat(), "title": "[SAMPLE] " + tpl.format(n=name),
                            "summary": "Synthetic headline for development. Not real news.", "url": None, "source": "Sample Wire",
                            "symbols": [sym], "is_sample": True})
        rs = _rng(f"macro{market}{now.date()}")
        for k, (title, _cat) in enumerate(_MACRO):
            ts = now - timedelta(hours=int(rs.integers(2, 96)))
            if ts >= since:
                out.append({"external_id": f"macro-{market}-{now.date()}-{k}", "published_at": ts.isoformat(), "title": "[SAMPLE] " + title,
                            "summary": "Synthetic market headline. Not real news.", "url": None, "source": "Sample Wire", "symbols": [], "is_sample": True,
                            "markets": [market]})
        return out


# recurring releases: (name, country, currency, category, impact, cadence_days, weekday_or_None, hour_utc)
_RELEASES = [
    ("RBI Monetary Policy Decision (sample)", "IN", "INR", "rates", "High", 56, None, 4), ("India CPI Inflation (sample)", "IN", "INR", "inflation", "High", 30, None, 11),
    ("India GDP Growth (sample)", "IN", "INR", "growth", "Medium", 91, None, 11), ("India Manufacturing PMI (sample)", "IN", "INR", "pmi", "Medium", 30, None, 5),
    ("FOMC Rate Decision (sample)", "US", "USD", "rates", "High", 49, 2, 18), ("US CPI (sample)", "US", "USD", "inflation", "High", 30, None, 12),
    ("US Nonfarm Payrolls (sample)", "US", "USD", "labour", "High", 30, 4, 12), ("US GDP (sample)", "US", "USD", "growth", "Medium", 91, None, 12),
    ("US ISM Manufacturing PMI (sample)", "US", "USD", "pmi", "Medium", 30, None, 14), ("ECB Rate Decision (sample)", "EU", "EUR", "rates", "High", 42, 3, 12),
    ("Euro Area CPI Flash (sample)", "EU", "EUR", "inflation", "Medium", 30, None, 9), ("BoE Rate Decision (sample)", "GB", "GBP", "rates", "High", 45, 3, 11),
    ("UK CPI (sample)", "GB", "GBP", "inflation", "Medium", 30, None, 6), ("BoJ Policy Decision (sample)", "JP", "JPY", "rates", "High", 45, None, 3),
    ("China Caixin PMI (sample)", "CN", "CNY", "pmi", "Low", 30, None, 1), ("US Initial Jobless Claims (sample)", "US", "USD", "labour", "Low", 7, 3, 12),
]


class SampleCalendarProvider(EconomicCalendarProvider, EarningsCalendarProvider):
    is_sample = True
    name = "sample"

    def get_events(self, start: date, end: date) -> List[dict]:
        out = []
        for name, country, ccy, cat, impact, cadence, wd, hour in _RELEASES:
            rs = _rng("eco" + name)
            anchor = date(2026, 1, 1) + timedelta(days=int(rs.integers(0, cadence)))
            d = anchor
            while d > start:
                d -= timedelta(days=cadence)
            while d <= end:
                if d >= start:
                    dd = d if wd is None else d + timedelta(days=(wd - d.weekday()) % 7)
                    r2 = _rng(f"{name}{dd}")
                    prev = round(float(r2.normal(4, 1.5)), 2)
                    fc = round(prev + float(r2.normal(0, 0.3)), 2)
                    past = dd < datetime.now(timezone.utc).date()
                    out.append({"external_id": f"{name}-{dd}", "event_time": datetime.combine(dd, time(hour), tzinfo=timezone.utc).isoformat(),
                                "country": country, "currency": ccy, "name": name, "category": cat, "impact": impact,
                                "actual": round(fc + float(r2.normal(0, 0.3)), 2) if past else None, "forecast": fc, "previous": prev,
                                "unit": "%", "source": SOURCE, "is_sample": True})
                d += timedelta(days=cadence)
        return out

    def get_earnings(self, symbols: List[str], start: date, end: date) -> List[dict]:
        out = []
        for sym in symbols:
            rs = _rng("earn" + sym)
            offset = int(rs.integers(0, 91))  # each company reports on its own quarterly cycle
            eps0, rev0 = float(rs.uniform(2, 60)), float(rs.uniform(500, 50000))
            d = date(2024, 1, 1) + timedelta(days=offset)
            q = 0
            while d <= end:
                if d >= start:
                    r2 = _rng(f"{sym}{d}")
                    est = round(eps0 * (1 + 0.02 * q), 2)
                    rest = round(rev0 * (1 + 0.025 * q), 1)
                    past = d < datetime.now(timezone.utc).date()
                    out.append({"symbol": sym, "event_date": d.isoformat(), "period": f"FQ{(q % 4) + 1} {d.year}", "time": ["bmo", "amc"][int(r2.integers(0, 2))],
                                "eps_estimate": est, "eps_actual": round(est * (1 + float(r2.normal(0.01, 0.08))), 2) if past else None,
                                "revenue_estimate": rest, "revenue_actual": round(rest * (1 + float(r2.normal(0.005, 0.04))), 1) if past else None,
                                "guidance": (["raised", "maintained", "lowered", None][int(r2.integers(0, 4))] if past else None),
                                "source": SOURCE, "is_sample": True})
                d += timedelta(days=91)
                q += 1
        return out
