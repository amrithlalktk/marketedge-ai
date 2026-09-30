"""Execution abstraction. Paper trading goes through PaperBroker; a real broker adapter
(e.g. an Indian broker's order API) implements the same interface later without touching
portfolios, alerts or the UI."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

import pandas as pd

from engine.paper import PaperOrder, close_at_market, mark_to_market, step


class BrokerAdapter(ABC):
    name = "abstract"
    live = False

    @abstractmethod
    def advance(self, order: PaperOrder, bars: pd.DataFrame) -> List[dict]:
        """Bring an order/position up to date with the latest market data; return new fills."""

    @abstractmethod
    def close(self, order: PaperOrder, bars: pd.DataFrame, requested_at: pd.Timestamp):
        ...

    def mark(self, order: PaperOrder, last_price: float) -> dict:
        return mark_to_market(order, last_price)


class PaperBroker(BrokerAdapter):
    """Simulated fills with the backtester's rules (engine.paper). No money moves."""
    name = "paper"

    def advance(self, order, bars):
        return step(order, bars)

    def close(self, order, bars, requested_at):
        return close_at_market(order, bars, requested_at)
