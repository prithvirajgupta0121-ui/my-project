"""Mission helpers."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session as DBSession

from ..models import Mission


def next_mission_code(db: DBSession, when: datetime | None = None) -> str:
    """AIM-YYYY-MMDD-NNN, sequential per calendar day."""
    when = when or datetime.now(timezone.utc)
    prefix = f"AIM-{when:%Y}-{when:%m%d}-"
    last = (db.query(Mission.code).filter(Mission.code.like(prefix + "%")).order_by(Mission.code.desc()).first())
    n = int(last[0].rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{n:03d}"
