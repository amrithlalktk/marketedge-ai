"""AI Market Analyst: explains setups using ONLY backend-supplied data.

* A context pack (setup payload, checks, historical evidence, regime, news, events)
  is built from the database; the model sees nothing else.
* The system prompt is stable and prompt-cached; per-request data goes in the user turn.
* Every number in the answer is checked against the context pack; numbers that
  cannot be found are returned as `unverified_numbers` and the answer is flagged.
* Without an API key (or if the call fails), a deterministic rule-based
  explanation is returned from the same context, and the response says so.
* Each Q&A is stored in `analyst_queries` with grounding result and token usage.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AnalystQuery, Instrument, Signal
from app.services.events_service import economic_events, earnings_for, news_for
from app.services.scan_service import latest_run
from engine.events import now_utc, relevant_events, upcoming_summary

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the MarketEdge AI market analyst. You explain trading setups produced by a rule-based analysis engine to the user.

Ground rules:
- Use ONLY the data inside the <context> JSON in the user's message. Never add prices, levels, statistics, news, events or facts that are not in it. If something the user asks about is not in the context, say it is not available in the platform's data.
- Quote numbers exactly as they appear in the context (you may round to fewer decimals). Do not compute new statistics or forecasts.
- Historical hit rates are evidence from backtests of the same rules, not predictions. Always state the sample size and backtest period next to any hit rate.
- Never say or imply that a trade is guaranteed, certain, "sure-shot" or risk-free, and do not tell the user what to do with their money. You explain what the engine found and what could invalidate it.
- If the context shows status NO_TRADE or no active setup, explain which checks blocked it rather than arguing for a trade.
- If the data is marked is_sample / SAMPLE, say clearly that it is synthetic development data, not real market data.

When explaining a setup, cover, in this order and only where the context has the data:
1. What triggered the setup (strategy, conditions met, patterns).
2. Which indicators agree and which disagree.
3. The market regime.
4. Support and resistance levels.
5. What would invalidate the setup (stop, invalidation rules, blocking/warning checks).
6. The historical evidence: how many similar historical trades, the period, T1/T2/stop rates, the conditioning used.
7. Risks: warnings, event risk (earnings, economic releases), news flow, data quality.

Keep the answer concise and structured with short headings. Finish with one line: "Historical performance does not guarantee future results."
"""


# ------------------------------------------------------------------ context
def _trim_setup(p: dict) -> dict:
    keep = ["symbol", "name", "market", "exchange", "currency", "sector", "as_of", "status", "direction", "setup_type", "current_price",
            "entry_zone", "entry_method", "stop", "stop_method", "targets", "target_methods", "risk_pct", "reward_pct_t1", "reward_pct_t2",
            "rr_t1", "rr_t2", "score", "score_label", "components", "score_notes", "trend", "momentum", "volume", "market_regime", "mtf",
            "expected_holding_days", "indicators", "inr", "pips", "derivatives", "events", "news", "market_cap_usd", "spread_bps", "data"]
    out = {k: p[k] for k in keep if k in p}
    out["strategy"] = {k: p["strategy"].get(k) for k in ("id", "name", "description", "conditions", "entry_rule", "stop_rule", "target_rule", "notes")}
    pr = p.get("probability") or {}
    out["historical_evidence"] = {k: pr.get(k) for k in ("sample_size", "backtest_period", "conditioning", "t1_hit_rate", "t1_ci95", "t2_hit_rate",
                                                         "stop_rate", "neither_rate", "expectancy_r", "avg_holding_bars", "symbols_covered", "definitions")}
    out["explanation"] = p.get("explanation")
    out["checks"] = p.get("checks")
    out["similar_historical_examples"] = (p.get("historical_examples") or [])[:5]
    return out


def build_context(db: Session, signal_id: Optional[int] = None, symbol: Optional[str] = None) -> Tuple[dict, Optional[Signal], Optional[str]]:
    sig = db.get(Signal, signal_id) if signal_id else None
    if signal_id and sig is None:
        raise LookupError("Setup not found")
    if sig is not None:
        symbol = sig.symbol if sig.market != "NFO" else None
    ins = db.scalar(select(Instrument).where(Instrument.symbol == symbol.upper())) if symbol else None
    if symbol and ins is None and sig is None:
        raise LookupError(f"Unknown symbol {symbol}")
    ctx: Dict[str, Any] = {"platform_disclaimer": "Historical/backtested statistics do not guarantee future results."}
    if sig is not None:
        ctx["setup"] = _trim_setup(sig.payload) if sig.market != "NFO" else {k: sig.payload.get(k) for k in (
            "status", "contract", "direction", "entry", "targets", "stop", "rr", "probability", "underlying", "checks", "reasons", "risk_factors",
            "level_method", "theta_burn_pct", "expected_move_hold", "data", "as_of")}
        ctx["setup"]["signal_id"] = sig.id
    elif ins is not None:
        run = latest_run(db, ins.market)
        rows = list(db.scalars(select(Signal).where(Signal.scan_run_id == run.id, Signal.symbol == ins.symbol))) if run else []
        ctx["instrument"] = {"symbol": ins.symbol, "name": ins.name, "market": ins.market, "exchange": ins.exchange, "currency": ins.currency,
                             "sector": ins.sector.name if ins.sector else None, "is_sample": ins.is_sample}
        ctx["active_setups"] = [_trim_setup(r.payload) for r in rows]
        if not rows:
            ctx["active_setups_note"] = "No strategy produced a setup for this instrument in the latest scan."
    if ins is not None:
        now = now_utc()
        ctx["recent_news"] = [{k: a[k] for k in ("title", "source", "published_at", "sentiment_label", "sentiment_score", "category", "is_sample")}
                              for a in news_for(db, symbols=[ins.symbol], since=now - timedelta(days=14), limit=8)]
        ctx["earnings"] = earnings_for(db, {ins.id: ins.symbol}, (now - timedelta(days=200)).date(), (now + timedelta(days=90)).date())[-4:]
        evs = relevant_events(economic_events(db, now - timedelta(hours=1), now + timedelta(days=7)), ins.market, {ins.currency})
        ctx["upcoming_economic_events"] = upcoming_summary(evs, now)
    return ctx, sig, symbol


# ------------------------------------------------------------------ grounding
_NUM = re.compile(r"(?<![A-Za-z_])[-+]?\d[\d,]*\.?\d*")


def _numbers_in(obj) -> List[float]:
    out: List[float] = []
    if isinstance(obj, dict):
        for v in obj.values():
            out.extend(_numbers_in(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(_numbers_in(v))
    elif isinstance(obj, bool) or obj is None:
        pass
    elif isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, str):
        out.extend(float(m.replace(",", "")) for m in _NUM.findall(obj) if m.replace(",", "").strip("+-.") != "")
    return out


def grounding_check(answer: str, ctx: dict) -> List[str]:
    """Numbers in the answer that do not match (within rounding) any number in the context."""
    known = _numbers_in(ctx)
    bad = []
    for raw in _NUM.findall(answer):
        s = raw.replace(",", "")
        if s.strip("+-.") == "":
            continue
        v = float(s)
        if abs(v) <= 10 and float(v).is_integer():
            continue  # list markers, section numbers, "3 days" style small integers
        d = len(s.split(".")[1]) if "." in s else 0
        # a quoted number must equal a context value rounded to the answer's own precision (or its % form)
        eps = 0.5 * 10 ** (-d) + 1e-9
        if not any(abs(v - k) <= eps or abs(v - 100 * k) <= eps for k in known):
            bad.append(raw)
    return sorted(set(bad))


# ------------------------------------------------------------------ answering
INTENTS = {
    "invalidation": ("invalidat", "stop", "wrong", "fail", "exit"),
    "risks": ("risk", "danger", "event", "earnings", "news", "warning", "concern"),
    "evidence": ("evidence", "histor", "probab", "hit rate", "backtest", "sample", "strong", "reliab"),
}


def question_intent(question: str) -> str:
    q = (question or "").lower()
    for intent, words in INTENTS.items():
        if any(w in q for w in words):
            return intent
    return "full"


def _instrument_context_lines(ctx: dict) -> List[str]:
    lines = []
    news = ctx.get("recent_news") or []
    if news:
        c = {k: sum(1 for a in news if a["sentiment_label"] == k) for k in ("Positive", "Neutral", "Negative")}
        lines.append(f"Recent news (14 days): {len(news)} articles — {c['Positive']} positive, {c['Neutral']} neutral, {c['Negative']} negative. "
                     "Sentiment is context only.")
        for a in news[:3]:
            lines.append(f"- {a['title']} ({a['source']}, {a['published_at'][:10]}, {a['sentiment_label']})")
    earn = [e for e in (ctx.get("earnings") or []) if not e.get("reported")]
    if earn:
        lines.append(f"Next earnings: {earn[0]['event_date']} ({earn[0].get('period')}).")
    for e in (ctx.get("upcoming_economic_events") or [])[:3]:
        lines.append(f"Upcoming {e['impact']}-impact release: {e['name']} ({e['event_time'][:16]} UTC).")
    return lines


def rule_based_answer(ctx: dict, question: str = "") -> str:
    """Deterministic explanation from the same context (used when the LLM is unavailable),
    focused on what the question asks (invalidation, risks, evidence, or everything)."""
    intent = question_intent(question)
    lines: List[str] = []
    setups = [ctx["setup"]] if "setup" in ctx else ctx.get("active_setups", [])
    if not setups:
        lines.append(ctx.get("active_setups_note", "No active setup in the latest scan."))
        lines.extend(_instrument_context_lines(ctx))
    for s in setups:
        if "contract" not in s and intent != "full":
            lines.extend(_focused(s, intent))
            continue
        if "contract" in s:
            lines.append(f"Option setup {s['contract'].get('label')}: status {s.get('status')}.")
            continue
        ev = s.get("historical_evidence", {})
        lines.append(f"## {s.get('symbol')} — {s.get('strategy', {}).get('name')} ({s.get('direction')}), status {s.get('status')}")
        lines.append(f"Trigger: {s.get('explanation', {}).get('trigger')}")
        if s.get("explanation", {}).get("agreeing"):
            lines.append("Agreeing: " + "; ".join(s["explanation"]["agreeing"]))
        if s.get("explanation", {}).get("disagreeing"):
            lines.append("Disagreeing: " + "; ".join(s["explanation"]["disagreeing"]))
        if s.get("market_regime"):
            lines.append(f"Market regime: {s['market_regime'].get('regime')} (volatility {s['market_regime'].get('volatility')}).")
        lines.append(f"Entry zone {s.get('entry_zone')}, stop {s.get('stop')} ({s.get('stop_method')}), targets {s.get('targets')}.")
        if ev.get("sample_size"):
            lines.append(f"Historical evidence: {ev['sample_size']} similar trades ({ev.get('conditioning')}), {ev.get('backtest_period')}; "
                         f"T1 hit rate {ev.get('t1_hit_rate')}%, T2 {ev.get('t2_hit_rate')}%, stop first {ev.get('stop_rate')}%.")
        blocks = [c["name"] + ": " + c["detail"] for c in s.get("checks") or [] if not c["passed"] and c["severity"] == "block"]
        warns = [c["detail"] for c in s.get("checks") or [] if not c["passed"] and c["severity"] == "warn"]
        if blocks:
            lines.append("Blocked by: " + "; ".join(blocks))
        if warns:
            lines.append("Risks: " + "; ".join(warns))
        for inv in s.get("explanation", {}).get("invalidation") or []:
            lines.append(f"Invalidation: {inv}")
    if setups and intent in ("full", "risks"):
        extra = _instrument_context_lines(ctx)
        if extra:
            lines.append("## Context")
            lines.extend(extra)
    lines.append("Historical performance does not guarantee future results.")
    return "\n".join(lines)


def _focused(s: dict, intent: str) -> List[str]:
    head = f"## {s.get('symbol')} — {s.get('strategy', {}).get('name')} ({s.get('direction')}), status {s.get('status')}"
    checks = s.get("checks") or []
    if intent == "invalidation":
        out = [head, f"Stop: {s.get('stop')} ({s.get('stop_method')})."]
        out += [f"- {inv}" for inv in s.get("explanation", {}).get("invalidation") or []]
        blocks = [c["name"] + ": " + c["detail"] for c in checks if not c["passed"] and c["severity"] == "block"]
        if blocks:
            out.append("Currently blocked by: " + "; ".join(blocks))
        return out
    if intent == "risks":
        out = [head]
        warns = [c["detail"] for c in checks if not c["passed"]]
        out += [f"- {w}" for w in warns] or ["- No failed checks recorded."]
        for e in (s.get("events") or {}).get("upcoming", [])[:4]:
            out.append(f"- Upcoming {e['impact']}-impact release: {e['name']} ({e['event_time'][:16]} UTC)")
        n = s.get("news") or {}
        if n.get("articles"):
            out.append(f"- News ({n['window_days']} days): {n['counts']} — context only")
        return out
    ev = s.get("historical_evidence", {})
    if not ev.get("sample_size"):
        return [head, "No comparable historical trades are available for this setup."]
    return [head, f"{ev['sample_size']} comparable historical trades ({ev.get('conditioning')}), {ev.get('backtest_period')}.",
            f"T1 before stop: {ev.get('t1_hit_rate')}% (95% CI {ev.get('t1_ci95')}); T2: {ev.get('t2_hit_rate')}%; stop first: {ev.get('stop_rate')}%; "
            f"neither: {ev.get('neither_rate')}%.", f"Expectancy after costs: {ev.get('expectancy_r')} R per trade; average hold {ev.get('avg_holding_bars')} bars.",
            "These are outcomes of the same rules on history, not a forecast."]


def _default_client(api_key: Optional[str]):
    import anthropic  # optional dependency (Python ≥ 3.10)

    s = get_settings()
    return anthropic.Anthropic(api_key=api_key, timeout=s.analyst_timeout_s, max_retries=2) if api_key else None


def _api_key() -> Optional[str]:
    import os

    from app.providers.registry import provider_secret

    return provider_secret("anthropic", "API_KEY") or get_settings().anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")


def _sdk_errors():
    """(rate_limit, status, connection) exception classes; placeholders if the SDK isn't installed."""
    try:
        import anthropic

        return anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError
    except ImportError:  # tests inject a fake client on interpreters without the SDK
        class _Never(Exception):
            pass
        return _Never, _Never, _Never


def call_llm(client, question: str, ctx: dict) -> Dict[str, Any]:
    RateLimitError, APIStatusError, APIConnectionError = _sdk_errors()
    s = get_settings()
    user = ("<context>\n" + json.dumps(ctx, default=str, sort_keys=True) + "\n</context>\n\n"
            f"Question: {question.strip()}")
    try:
        resp = client.beta.messages.create(
            model=s.analyst_model,
            max_tokens=s.analyst_max_tokens,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],  # stable → cached
            messages=[{"role": "user", "content": user}],
            output_config={"effort": s.analyst_effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # server-side refusal fallback, routed by refusal category
        )
    except RateLimitError as exc:
        raise RuntimeError(f"AI analyst rate limited by the provider; retry later ({getattr(exc, 'status_code', 429)})") from exc
    except APIStatusError as exc:
        raise RuntimeError(f"AI analyst API error {getattr(exc, 'status_code', '?')}") from exc
    except APIConnectionError as exc:
        raise RuntimeError("AI analyst unreachable (network)") from exc
    u = resp.usage
    usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
             "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", None) or 0,
             "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", None) or 0}
    if resp.stop_reason == "refusal":
        return {"text": None, "stop_reason": "refusal", "model": resp.model, "usage": usage}
    text = "".join(b.text for b in resp.content if b.type == "text")
    return {"text": text, "stop_reason": resp.stop_reason, "model": resp.model, "usage": usage}


def ask(db: Session, user_id: Optional[int], question: str, signal_id: Optional[int] = None, symbol: Optional[str] = None,
        client_factory: Optional[Callable[[Optional[str]], Any]] = None) -> dict:
    ctx, sig, symbol = build_context(db, signal_id, symbol)
    mode, model, stop_reason, usage, note = "rule_based", None, None, {}, None
    answer = None
    key = _api_key()
    client = None
    if key:
        try:
            client = (client_factory or _default_client)(key)  # resolved at call time (overridable in tests)
        except ImportError:
            note = "AI SDK not installed in this environment."
    if client is not None:
        try:
            r = call_llm(client, question, ctx)
            model, stop_reason, usage = r["model"], r["stop_reason"], r["usage"]
            if r["text"]:
                answer, mode = r["text"], "llm"
            else:
                note = "The AI model declined this request; showing the rule-based explanation instead."
        except RuntimeError as exc:
            note = f"{exc}; showing the rule-based explanation instead."
    elif note is None:
        note = "AI analyst not configured (no API key); showing the rule-based explanation from the same data."
    if answer is None:
        answer = rule_based_answer(ctx, question)
    unverified = grounding_check(answer, ctx) if mode == "llm" else []
    row = AnalystQuery(user_id=user_id, signal_id=sig.id if sig else None, symbol=symbol, question=question[:2000], answer=answer,
                       mode=mode, model=model, stop_reason=stop_reason, grounded=not unverified, unverified_numbers=unverified, usage=usage)
    db.add(row)
    db.commit()
    return {"id": row.id, "mode": mode, "model": model, "answer": answer, "grounded": not unverified, "unverified_numbers": unverified,
            "note": note, "usage": usage, "context_used": {"keys": sorted(ctx), "signal_id": sig.id if sig else None, "symbol": symbol},
            "grounding_note": ("Every number in the answer matched the platform's data." if not unverified else
                               "Some numbers in the answer could not be matched to the platform's data — treat them with caution.")
            if mode == "llm" else "Rule-based explanation generated directly from the platform's data.",
            "disclaimer": "Historical/backtested performance and probability estimates do not guarantee future results. "
                          "The AI analyst explains platform data; it is not investment advice."}
