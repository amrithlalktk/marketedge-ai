"""Explainable headline sentiment and category classification.

A small financial lexicon (in the spirit of Loughran–McDonald) with phrase
matching and simple negation handling. Every label comes with the exact terms
that produced it, so a reader can audit the classification. Sentiment is
CONTEXT for a setup; it never creates or scores a trade.
"""
from __future__ import annotations

import re
from typing import Dict, List

POSITIVE = [
    "beats estimates", "beat estimates", "beats", "record profit", "record revenue", "raises guidance", "raised guidance", "upgrade", "upgraded",
    "outperform", "strong demand", "surge", "surges", "soars", "jumps", "rally", "wins contract", "bags order", "order win", "approval", "approved",
    "expansion", "buyback", "dividend hike", "raises dividend", "profit rises", "profit jumps", "revenue grows", "growth accelerates", "upbeat",
    "exceeds", "tops estimates", "all-time high", "partnership", "acquires", "debt-free", "rating upgrade", "inflows",
]
NEGATIVE = [
    "misses estimates", "missed estimates", "misses", "cuts guidance", "cut guidance", "lowers guidance", "downgrade", "downgraded", "underperform",
    "plunge", "plunges", "slumps", "tumbles", "falls", "crash", "loss widens", "net loss", "profit falls", "revenue declines", "weak demand",
    "probe", "investigation", "raid", "fraud", "lawsuit", "sued", "penalty", "fined", "default", "defaults", "recall", "resigns", "resignation",
    "ban", "banned", "halt", "halted", "suspended", "delisting", "sanctions", "outflows", "warning", "profit warning", "strike", "shutdown",
    "rating cut", "insolvency", "bankruptcy", "layoffs",
]
NEGATORS = {"not", "no", "never", "denies", "without", "unlikely"}

CATEGORIES: Dict[str, List[str]] = {
    "earnings": ["results", "quarter", "q1", "q2", "q3", "q4", "eps", "earnings", "revenue", "profit", "guidance", "estimates"],
    "regulatory": ["sebi", "sec", "regulator", "rbi directive", "probe", "fine", "fined", "penalty", "approval", "licence", "license", "court", "ban"],
    "insider": ["insider", "promoter", "stake sale", "pledge", "buys shares", "sells shares", "director"],
    "analyst": ["upgrade", "downgrade", "target price", "rating", "outperform", "underperform", "initiates coverage", "brokerage"],
    "macro": ["inflation", "cpi", "gdp", "rate hike", "rate cut", "rbi", "fed", "ecb", "boj", "pmi", "jobs report", "payrolls", "yields"],
    "geopolitical": ["war", "sanctions", "tariff", "election", "conflict", "border", "geopolitical", "embargo"],
    "announcement": ["launch", "contract", "order", "acquisition", "acquires", "merger", "dividend", "buyback", "partnership", "expansion", "listing"],
}


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9\-']+", text.lower())


def _find(text_l: str, toks: List[str], lexicon: List[str]) -> List[tuple]:
    """Longest-phrase-first matching; returns (term, negated)."""
    hits, used = [], [False] * len(toks)
    for phrase in sorted(lexicon, key=lambda p: -len(p.split())):
        p = phrase.split()
        for i in range(len(toks) - len(p) + 1):
            if toks[i : i + len(p)] == p and not any(used[i : i + len(p)]):
                for j in range(i, i + len(p)):
                    used[j] = True
                negated = any(t in NEGATORS for t in toks[max(0, i - 3) : i])
                hits.append((phrase, negated))
    return hits


def classify(title: str, summary: str = "") -> Dict:
    text = f"{title}. {summary or ''}"
    toks = _tokens(text)
    pos = _find(text.lower(), toks, POSITIVE)
    neg = _find(text.lower(), toks, NEGATIVE)
    # title terms count double: headlines carry the market-moving claim
    title_toks = set(_tokens(title))
    w = lambda term: 2.0 if set(term.split()) <= title_toks else 1.0
    p_score = sum(w(t) * (-1 if n else 1) for t, n in pos)
    n_score = sum(w(t) * (-1 if n else 1) for t, n in neg)
    total = abs(p_score) + abs(n_score)
    score = 0.0 if total == 0 else (p_score - n_score) / (total + 1.0)
    label = "Positive" if score >= 0.2 else "Negative" if score <= -0.2 else "Neutral"
    cats = [c for c, kws in CATEGORIES.items() if any(k in text.lower() for k in kws)]
    return {
        "label": label, "score": round(score, 3), "method": "lexicon-v1",
        "terms": {"positive": [t + (" (negated)" if n else "") for t, n in pos], "negative": [t + (" (negated)" if n else "") for t, n in neg]},
        "category": cats[0] if cats else "general", "categories": cats,
    }
