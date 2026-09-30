from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.schemas import PortfolioRiskIn, PositionSizeIn
from engine import risk

router = APIRouter(prefix="/risk", tags=["risk"], dependencies=[Depends(get_current_user)])


@router.post("/position-size")
def position_size(body: PositionSizeIn):
    try:
        if body.stop is not None:
            out = risk.position_size(body.capital, body.risk_pct, body.entry, body.stop, body.lot_size, body.max_position_pct)
            out["method"] = "Fixed % risk with given stop"
        elif body.atr is not None:
            out = risk.volatility_position_size(body.capital, body.risk_pct, body.entry, body.atr, body.atr_mult, body.direction, body.lot_size)
        else:
            raise HTTPException(422, "Provide either stop or atr")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return out


@router.post("/portfolio")
def portfolio(body: PortfolioRiskIn):
    try:
        return risk.portfolio_risk(body.capital, body.positions)
    except (KeyError, TypeError) as exc:
        raise HTTPException(422, f"Each position needs quantity, entry and stop ({exc})") from exc
