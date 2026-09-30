from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models import AuditLog


def audit(db: Session, action: str, user_id: Optional[int] = None, target: str = "", detail: Optional[dict] = None, ip: str = "", commit: bool = True) -> None:
    db.add(AuditLog(user_id=user_id, action=action, target=target[:255], detail=detail or {}, ip=ip[:64]))
    if commit:
        db.commit()
