"""Custom strategy persistence with immutable versions."""
from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Strategy, StrategyVersion
from engine.strategies import STRATEGIES, StrategySpec, build_custom_strategy


def spec_for(row: Strategy, version: Optional[StrategyVersion] = None) -> StrategySpec:
    v = version or next((x for x in row.versions if x.version == row.current_version), None)
    defn = dict(v.definition if v else row.definition)
    spec = build_custom_strategy({**defn, "id": row.key, "name": row.name, "direction": row.direction})
    spec.notes = f"Custom strategy v{v.version if v else row.current_version}"
    return spec


def resolve(db: Session, key: str, version: Optional[int] = None) -> Tuple[StrategySpec, Optional[int]]:
    if key in STRATEGIES:
        return STRATEGIES[key], None
    row = db.scalar(select(Strategy).where(Strategy.key == key, Strategy.is_active.is_(True)))
    if row is None:
        raise ValueError(f"Unknown strategy {key!r}")
    ver = None
    if version is not None:
        ver = next((x for x in row.versions if x.version == version), None)
        if ver is None:
            raise ValueError(f"Strategy {key!r} has no version {version}")
    ver = ver or next((x for x in row.versions if x.version == row.current_version), None)
    return spec_for(row, ver), (ver.id if ver else None)


def scan_strategies(db: Session) -> List[StrategySpec]:
    """Built-ins plus custom strategies an analyst switched on for the daily scan."""
    custom = [spec_for(r) for r in db.scalars(select(Strategy).where(Strategy.is_active.is_(True), Strategy.include_in_scan.is_(True),
                                                                       Strategy.is_builtin.is_(False)))]
    return list(STRATEGIES.values()) + custom


def create(db: Session, key: str, defn: dict, owner_id: int, note: str = "") -> Strategy:
    build_custom_strategy(defn)  # validate
    row = Strategy(key=key, name=defn["name"], direction=defn["direction"], definition=defn, owner_id=owner_id, current_version=1)
    db.add(row)
    db.flush()
    db.add(StrategyVersion(strategy_id=row.id, version=1, definition=defn, note=note, created_by=owner_id))
    db.commit()
    db.refresh(row)
    return row


def new_version(db: Session, row: Strategy, defn: dict, user_id: int, note: str = "") -> StrategyVersion:
    build_custom_strategy(defn)
    v = StrategyVersion(strategy_id=row.id, version=row.current_version + 1, definition=defn, note=note, created_by=user_id)
    db.add(v)
    row.current_version, row.definition, row.name, row.direction = v.version, defn, defn["name"], defn["direction"]
    db.commit()
    db.refresh(row)
    return v
