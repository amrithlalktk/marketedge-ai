#!/usr/bin/env python3
"""Read-path load test for the MarketEdge AI API.

Logs in once, then N concurrent virtual users request the endpoints behind the dashboard,
scanner, stock-detail and options pages for a fixed duration. The script reports the
throughput and p50/p95/p99 latency for each endpoint, and exits non-zero if the error rate
or p95 exceeds its limit.

    backend/.venv/bin/python scripts/loadtest.py --base http://localhost:8000 \
        --email admin@example.com --password '...' --users 20 --duration 60

It only calls this app's own API (no paid provider calls; data comes from what is already
ingested). The rate limiter counts requests per client IP (120/min by default), so point it at
an instance started with a raised RATE_LIMIT_PER_MINUTE — see docs/OPERATIONS.md.
"""
from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
import time
from collections import defaultdict

import httpx

ENDPOINTS = [  # (weight, path) — roughly what the UI requests per page view
    (5, "/api/v1/signals/top?market={market}"),
    (4, "/api/v1/signals?market={market}&limit=50"),
    (3, "/api/v1/markets/overview?market={market}"),
    (2, "/api/v1/markets/regime?market={market}"),
    (2, "/api/v1/markets/breadth?market={market}"),
    (2, "/api/v1/markets/sectors?market={market}"),
    (3, "/api/v1/stocks/{symbol}/analysis"),
    (3, "/api/v1/stocks/{symbol}/candles"),
    (1, "/api/v1/signals/track-record?market={market}"),
    (1, "/api/v1/options/nifty"),
]


def pct(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--market", default="NSE")
    ap.add_argument("--users", type=int, default=20)
    ap.add_argument("--duration", type=float, default=60)
    ap.add_argument("--max-p95-ms", type=float, default=800)
    ap.add_argument("--max-error-rate", type=float, default=0.01)
    a = ap.parse_args()

    async with httpx.AsyncClient(base_url=a.base, timeout=30) as c:
        r = await c.post("/api/v1/auth/login", json={"email": a.email, "password": a.password})
        r.raise_for_status()
        headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        r = await c.get(f"/api/v1/stocks?market={a.market}&limit=50", headers=headers)
        r.raise_for_status()
        body = r.json()
        items = body.get("items", body) if isinstance(body, dict) else body
        symbols = [i["symbol"] for i in items][:50] or ["DEMO_001"]

    paths = [p for w, p in ENDPOINTS for _ in range(w)]
    lat = defaultdict(list)
    errors = defaultdict(int)
    deadline = time.perf_counter() + a.duration
    limits = httpx.Limits(max_connections=a.users, max_keepalive_connections=a.users)

    async def user(c: httpx.AsyncClient):
        while time.perf_counter() < deadline:
            tpl = random.choice(paths)
            path = tpl.format(market=a.market, symbol=random.choice(symbols))
            t0 = time.perf_counter()
            try:
                r = await c.get(path, headers=headers)
                ok = r.status_code < 400
            except httpx.HTTPError:
                ok = False
            lat[tpl].append((time.perf_counter() - t0) * 1000)
            if not ok:
                errors[tpl] += 1
            await asyncio.sleep(random.uniform(0.05, 0.25))  # think time

    t_start = time.perf_counter()
    async with httpx.AsyncClient(base_url=a.base, timeout=30, limits=limits) as c:
        await asyncio.gather(*(user(c) for _ in range(a.users)))
    elapsed = time.perf_counter() - t_start

    total = sum(len(v) for v in lat.values())
    total_err = sum(errors.values())
    every = [x for v in lat.values() for x in v]
    print(f"\n{a.users} users, {elapsed:.0f}s, {total} requests, {total / elapsed:.1f} req/s, errors {total_err} ({total_err / max(total, 1):.2%})")
    print(f"{'endpoint':58} {'n':>6} {'p50':>8} {'p95':>8} {'p99':>8} {'err':>5}")
    for tpl, xs in sorted(lat.items(), key=lambda kv: -pct(kv[1], 95)):
        print(f"{tpl[:58]:58} {len(xs):6d} {statistics.median(xs):7.0f}ms {pct(xs, 95):7.0f}ms {pct(xs, 99):7.0f}ms {errors[tpl]:5d}")
    p95 = pct(every, 95)
    print(f"{'ALL':58} {total:6d} {statistics.median(every):7.0f}ms {p95:7.0f}ms {pct(every, 99):7.0f}ms {total_err:5d}")
    failed = p95 > a.max_p95_ms or total_err / max(total, 1) > a.max_error_rate
    print("RESULT:", "FAIL" if failed else "PASS", f"(limits p95 ≤ {a.max_p95_ms:.0f}ms, errors ≤ {a.max_error_rate:.0%})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
