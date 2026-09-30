from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from engine.config import DEFAULT_WEIGHTS, LevelConfig


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    full_name: str = Field(default="", max_length=255)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)
    totp_code: Optional[str] = Field(default=None, max_length=10)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    permissions: List[str]
    totp_enabled: bool


class TotpCode(BaseModel):
    code: str = Field(min_length=6, max_length=10)


class TotpDisable(BaseModel):
    code: str = Field(min_length=6, max_length=10)
    password: str = Field(max_length=128)


class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    # the six market ids, plus the legacy list categories kept for existing watchlists
    market: str = Field(default="NSE", pattern="^(NSE|CRYPTO|US|EUROPE|ASIA|FX|NIFTY|GLOBAL|FOREX|MIXED)$")


class WatchlistItemIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=64)
    tags: List[str] = Field(default_factory=list, max_length=10)
    note: str = Field(default="", max_length=500)

    @field_validator("tags")
    @classmethod
    def _tags(cls, v):
        return [t.strip()[:32] for t in v if t.strip()]


class WatchlistItemPatch(BaseModel):
    tags: Optional[List[str]] = None
    note: Optional[str] = Field(default=None, max_length=500)


class PositionSizeIn(BaseModel):
    capital: float = Field(gt=0)
    risk_pct: float = Field(gt=0, le=100)
    entry: float = Field(gt=0)
    stop: Optional[float] = Field(default=None, gt=0)
    atr: Optional[float] = Field(default=None, gt=0)
    atr_mult: float = Field(default=2.0, gt=0, le=10)
    direction: str = Field(default="LONG", pattern="^(LONG|SHORT)$")
    lot_size: int = Field(default=1, ge=1)
    max_position_pct: Optional[float] = Field(default=None, gt=0, le=100)


class PortfolioRiskIn(BaseModel):
    capital: float = Field(gt=0)
    positions: List[Dict[str, Any]] = Field(max_length=500)


class Condition(BaseModel):
    left: Any
    op: str
    right: Any


class Exits(BaseModel):
    stop_method: str = Field(default="structure", pattern="^(structure|atr)$")
    atr_stop_mult: Optional[float] = Field(default=None, ge=0.5, le=6)
    t1_r: Optional[float] = Field(default=None, ge=0.5, le=5)
    t2_r: Optional[float] = Field(default=None, ge=1, le=10)
    max_hold_bars: Optional[int] = Field(default=None, ge=1, le=120)


class StrategyDefinition(BaseModel):
    """Operands are numbers, feature names or {"feature", "mult", "shift"}; the engine validates them."""
    name: str = Field(min_length=1, max_length=128)
    direction: str = Field(pattern="^(LONG|SHORT)$")
    conditions: List[Condition] = Field(default_factory=list, max_length=12)
    any_of: Optional[List[List[Condition]]] = Field(default=None, max_length=4)
    exits: Optional[Exits] = None
    max_hold_bars: int = Field(default=20, ge=1, le=120)

    def engine_dict(self) -> dict:
        d = self.model_dump(exclude_none=True)
        if self.exits:
            d["exits"] = self.exits.model_dump(exclude_none=True)
        return d


class StrategyIn(StrategyDefinition):
    key: str = Field(pattern=r"^[a-z0-9_]{3,64}$")
    note: str = Field(default="", max_length=500)


class StrategyVersionIn(StrategyDefinition):
    note: str = Field(default="", max_length=500)


class StrategyPatch(BaseModel):
    include_in_scan: Optional[bool] = None
    is_active: Optional[bool] = None


_LEVEL_FIELDS = set(LevelConfig().__dict__)


class PortfolioIn(BaseModel):
    initial_capital: float = Field(default=1_000_000, ge=10_000, le=1e11)
    max_positions: int = Field(default=10, ge=1, le=100)
    max_position_pct: float = Field(default=20, gt=0, le=100)
    max_sector_pct: Optional[float] = Field(default=None, gt=0, le=100)


class BacktestIn(BaseModel):
    market: str = Field(default="NSE", pattern="^(NSE|CRYPTO|US|EUROPE|ASIA|FX)$")
    strategy_key: Optional[str] = Field(default=None, max_length=64)
    strategy_version: Optional[int] = Field(default=None, ge=1)
    portfolio: Optional[PortfolioIn] = Field(default_factory=PortfolioIn)
    definition: Optional[StrategyDefinition] = None
    symbols: Optional[List[str]] = Field(default=None, max_length=3000)
    start: Optional[date] = None
    end: Optional[date] = None
    commission_pct: Optional[float] = Field(default=None, ge=0, le=5)  # None → the market's cost model
    slippage_pct: Optional[float] = Field(default=None, ge=0, le=5)
    risk_per_trade_pct: float = Field(default=1.0, gt=0, le=10)
    partial_at_t1: float = Field(default=0.5, ge=0, le=1)
    max_hold_bars: Optional[int] = Field(default=None, ge=1, le=120)
    walk_forward: bool = True
    train_years: float = Field(default=3, ge=0.5, le=15)
    test_years: float = Field(default=1, ge=0.25, le=5)
    grid: Optional[Dict[str, List[float]]] = None

    @field_validator("grid")
    @classmethod
    def _grid(cls, v):
        if v is None:
            return v
        bad = set(v) - _LEVEL_FIELDS
        if bad:
            raise ValueError(f"Grid keys must be level parameters: {sorted(_LEVEL_FIELDS)}")
        if sum(len(x) for x in v.values()) > 12:
            raise ValueError("Grid too large (max 12 values in total)")
        return v


class ScoringSettingsIn(BaseModel):
    weights: Optional[Dict[str, float]] = None
    labels: Optional[List[List[Any]]] = None
    validation: Optional[Dict[str, float]] = None
    levels: Optional[Dict[str, float]] = None

    @field_validator("weights")
    @classmethod
    def _w(cls, v):
        if v is None:
            return v
        if set(v) - set(DEFAULT_WEIGHTS):
            raise ValueError(f"Unknown weight keys; allowed: {sorted(DEFAULT_WEIGHTS)}")
        if any(x < 0 or x > 100 for x in v.values()):
            raise ValueError("Weights must be between 0 and 100")
        return v

    @field_validator("labels")
    @classmethod
    def _l(cls, v):
        if v is None:
            return v
        for item in v:
            if len(item) != 2 or not isinstance(item[0], (int, float)) or not isinstance(item[1], str):
                raise ValueError("labels must be [[min_score, label], ...]")
        return v


class RoleChange(BaseModel):
    role: str = Field(pattern="^(standard|premium|analyst|admin)$")
    is_active: Optional[bool] = None


class ProviderKeyIn(BaseModel):
    provider: str = Field(pattern=r"^[a-z0-9_]{2,64}$")
    name: str = Field(pattern=r"^[A-Za-z0-9_]{2,64}$")
    value: str = Field(min_length=1, max_length=4096)


class StrategyControlIn(BaseModel):
    enabled: bool
    reason: str = Field(default="", max_length=500)
