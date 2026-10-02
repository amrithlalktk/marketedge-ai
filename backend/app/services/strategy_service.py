"""Built-in strategies (the custom strategy builder was removed in the lite build)."""
from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from engine.strategies import STRATEGIES, StrategySpec


def resolve(db: Optional[Session], key: str, version: Optional[int] = None) -> Tuple[StrategySpec, Optional[int]]:
    if key in STRATEGIES:
        return STRATEGIES[key], None
    raise ValueError(f"Unknown strategy {key!r}")


def scan_strategies(db: Optional[Session] = None) -> List[StrategySpec]:
    return list(STRATEGIES.values())
