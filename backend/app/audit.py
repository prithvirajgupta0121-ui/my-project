"""Audit logging and security-event recording."""
from __future__ import annotations

import logging
import secrets

from sqlalchemy.orm import Session as DBSession

from .database import db_session
from .models import AuditLog, Severity, SystemEvent
from .realtime import hub

log = logging.getLogger("aegis.audit")


def _event_id() -> str:
    return "AUD-" + secrets.token_hex(4).upper()


def record(
    db: DBSession | None,
    action: str,
    *,
    resource: str = "",
    result: str = "SUCCESS",
    severity: str = Severity.INFO.value,
    user=None,
    username: str | None = None,
    session_ref: str = "",
    ip: str = "",
    details: dict | None = None,
    commit: bool = True,
) -> None:
    """Write an audit record. Never stores secrets (passwords, tokens) in details."""
    entry = AuditLog(
        event_id=_event_id(),
        user_id=getattr(user, "id", None),
        username=username or getattr(user, "username", None) or "anonymous",
        action=action,
        resource=resource[:160],
        ip=ip[:64],
        session_ref=session_ref,
        result=result,
        severity=severity,
        details=details or {},
    )

    def _write(d: DBSession):
        d.add(entry)
        if commit:
            d.commit()

    if db is None:
        with db_session() as d:
            _write(d)
    else:
        _write(db)
    log.info("audit %s %s %s %s", entry.action, entry.username, entry.resource, entry.result)
    if severity in (Severity.WARNING.value, Severity.HIGH.value, Severity.CRITICAL.value) or result != "SUCCESS":
        hub.publish({
            "type": "security", "action": action, "result": result, "severity": severity,
            "user": entry.username, "resource": entry.resource,
        }, audience="admin")


def system_event(category: str, message: str, severity: str = Severity.INFO.value,
                 mission_id: int | None = None, details: dict | None = None) -> None:
    with db_session() as d:
        d.add(SystemEvent(category=category, message=message, severity=severity,
                          mission_id=mission_id, details=details or {}))
