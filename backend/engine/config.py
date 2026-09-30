"""Engine configuration. Every threshold used to accept/reject a setup lives here
so results are reproducible and administrators can tune them explicitly."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional


DEFAULT_WEIGHTS: Dict[str, float] = {
    "trend": 20,
    "momentum": 15,
    "volume": 15,
    "price_action": 15,
    "structure": 10,
    "fundamental": 10,
    "volatility": 5,
    "regime": 5,
    "risk_reward": 5,
    # ensemble components (§45) — visible on every setup; weight 0 by default so they do not
    # change the score until an administrator opts in
    "historical": 0,
    "ml": 0,
}

# (minimum score, label). Evaluated top-down. Admin-configurable.
DEFAULT_LABELS: List[List] = [
    [90, "Very strong setup"],
    [75, "Strong setup"],
    [60, "Moderate setup"],
    [0, "Do not recommend"],
]


@dataclass
class CostModel:
    """Round-trip costs are applied as a percentage of traded value per side.

    Defaults approximate Indian equity delivery: brokerage + STT + exchange
    charges + GST + stamp duty (~0.12%/side) plus slippage.
    """

    commission_pct: float = 0.12
    slippage_pct: float = 0.05


@dataclass
class LevelConfig:
    atr_stop_mult: float = 2.0
    min_stop_atr: float = 0.8
    max_stop_atr: float = 3.5
    swing_buffer_atr: float = 0.2
    swing_lookback: int = 20
    t1_r: float = 1.5
    t2_r: float = 3.0
    t3_r: float = 4.5
    resistance_lookback: int = 250
    max_chase_atr: float = 0.5  # skip if next open gaps more than this beyond signal close


@dataclass
class ValidationConfig:
    min_history_bars: int = 260
    min_avg_traded_value: float = 5e7  # 5 crore INR over 20 days (average/day)
    min_price: float = 20.0
    min_rr_t2: float = 2.0
    max_stop_pct: float = 10.0
    min_sample_size: int = 30
    min_score: float = 60.0
    max_staleness_days: int = 4  # calendar days since the expected last session
    earnings_block_days: int = 3
    earnings_warn_days: int = 10
    abnormal_atr_percentile: float = 0.98
    suspicious_volume_mult: float = 10.0


@dataclass
class BacktestConfig:
    max_hold_bars: int = 20
    partial_at_t1: float = 0.5  # fraction closed at T1; stop moves to breakeven afterwards
    risk_per_trade_pct: float = 1.0  # used for the equity curve (fixed-fractional)
    costs: CostModel = field(default_factory=CostModel)


@dataclass
class EngineConfig:
    weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    labels: List[List] = field(default_factory=lambda: [list(x) for x in DEFAULT_LABELS])
    levels: LevelConfig = field(default_factory=LevelConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    analysis_mode: str = "hybrid"  # technical | fundamental | hybrid

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_overrides(cls, overrides: Optional[dict]) -> "EngineConfig":
        cfg = cls()
        if not overrides:
            return cfg
        if "weights" in overrides:
            cfg.weights.update({k: float(v) for k, v in overrides["weights"].items() if k in DEFAULT_WEIGHTS})
        if "labels" in overrides:
            cfg.labels = sorted([[float(a), str(b)] for a, b in overrides["labels"]], key=lambda x: -x[0])
        for section in ("levels", "validation"):
            for k, v in (overrides.get(section) or {}).items():
                if hasattr(getattr(cfg, section), k):
                    setattr(getattr(cfg, section), k, type(getattr(getattr(cfg, section), k))(v))
        for k, v in (overrides.get("backtest") or {}).items():
            if k == "costs":
                for ck, cv in v.items():
                    if hasattr(cfg.backtest.costs, ck):
                        setattr(cfg.backtest.costs, ck, float(cv))
            elif hasattr(cfg.backtest, k):
                setattr(cfg.backtest, k, type(getattr(cfg.backtest, k))(v))
        if overrides.get("analysis_mode") in ("technical", "fundamental", "hybrid"):
            cfg.analysis_mode = overrides["analysis_mode"]
        return cfg

    def label_for(self, score: float) -> str:
        for threshold, label in sorted(self.labels, key=lambda x: -x[0]):
            if score >= threshold:
                return label
        return self.labels[-1][1]


# Index setups feed 1–3 session option trades, so their stop/target geometry is
# sized for that horizon (tighter ATR stop, nearer targets). The SAME config is
# used when replaying index history, so hit rates describe exactly these levels.
INDEX_LEVELS = LevelConfig(atr_stop_mult=1.2, min_stop_atr=0.6, max_stop_atr=1.8, swing_buffer_atr=0.1, swing_lookback=10,
                           t1_r=1.5, t2_r=2.5, t3_r=3.5, max_chase_atr=0.3)
