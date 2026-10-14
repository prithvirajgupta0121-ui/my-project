"""SECURITY & AUDIT CENTER endpoints."""
from __future__ import annotations

from collections import Counter
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_

from ..config import get_settings
from ..deps import Ctx, require
from ..errors import APIError
from ..middleware import api_stats
from ..models import AuditLog, Session, Severity, SystemEvent, User
from ..realtime import hub
from ..security import now_utc

router = APIRouter(prefix="/api/security", tags=["security"])

LOGIN_ACTIONS = ("LOGIN_SUCCESS", "LOGIN_FAILED", "LOGIN_BLOCKED_LOCKED", "LOGIN_RATE_LIMITED", "DEMO_MODE_ENTERED")


def _audit_out(a: AuditLog) -> dict:
    return {"event_id": a.event_id, "user": a.username, "timestamp": a.timestamp.isoformat(), "action": a.action,
            "resource": a.resource, "ip": a.ip, "session": a.session_ref, "result": a.result, "severity": a.severity,
            "details": a.details or {}}


def _active_sessions_q(db):
    now = now_utc()
    idle = timedelta(minutes=get_settings().session_idle_minutes)
    return db.query(Session).filter(Session.revoked_at.is_(None), Session.expires_at > now,
                                    Session.last_seen_at > now - idle)


@router.get("/overview")
def overview(ctx: Ctx = Depends(require("audit:read"))):
    db = ctx.db
    since = now_utc() - timedelta(hours=24)
    s = get_settings()
    failed = db.query(func.count(AuditLog.id)).filter(AuditLog.action == "LOGIN_FAILED", AuditLog.timestamp >= since).scalar()
    sec_events = db.query(func.count(AuditLog.id)).filter(
        AuditLog.timestamp >= since,
        or_(AuditLog.severity.in_([Severity.WARNING.value, Severity.HIGH.value, Severity.CRITICAL.value]),
            AuditLog.result != "SUCCESS")).scalar()
    locked = db.query(func.count(User.id)).filter(User.locked_until > now_utc()).scalar()
    by_sev = dict(db.query(AuditLog.severity, func.count(AuditLog.id)).filter(AuditLog.timestamp >= since)
                  .group_by(AuditLog.severity).all())
    logins = db.query(AuditLog).filter(AuditLog.action.in_(LOGIN_ACTIONS)).order_by(AuditLog.timestamp.desc()).limit(12).all()
    hourly = Counter()
    for (ts, res) in db.query(AuditLog.timestamp, AuditLog.result).filter(AuditLog.timestamp >= since).all():
        hourly[(ts.strftime("%H:00"), res)] += 1
    return {
        "components": [
            {"name": "AUTHENTICATION", "status": "OPERATIONAL", "detail": "Argon2id · session tokens hashed (SHA-256)"},
            {"name": "DATABASE", "status": "PROTECTED", "detail": "Sensitive fields encrypted (Fernet)" if s.encrypt_at_rest else "Encryption disabled"},
            {"name": "FILE STORAGE", "status": "LOCAL", "detail": ("Encrypted at rest · 0600 permissions" if s.encrypt_at_rest else "0600 permissions")},
            {"name": "NETWORK", "status": "MONITORED", "detail": "No cloud upload · CSRF + CORS allow-list · rate limiting"},
            {"name": "AUDIT LOG", "status": "ACTIVE", "detail": "All security-relevant actions recorded"},
        ],
        "active_sessions": _active_sessions_q(db).count(),
        "failed_logins_24h": failed,
        "security_events_24h": sec_events,
        "locked_accounts": locked,
        "severity_24h": by_sev,
        "recent_logins": [_audit_out(a) for a in logins],
        "hourly": [{"hour": h, "result": r, "count": c} for (h, r), c in sorted(hourly.items())],
        "api": api_stats.snapshot(),
        "ws_clients": hub.client_count,
    }


@router.get("/audit")
def audit_log(ctx: Ctx = Depends(require("audit:read")), action: str | None = Query(None, max_length=48),
              result: str | None = Query(None, pattern=r"^(SUCCESS|FAILURE|DENIED)$"),
              severity: str | None = Query(None, pattern=r"^(INFO|WARNING|HIGH|CRITICAL)$"),
              q: str | None = Query(None, max_length=64), category: str | None = Query(None, max_length=24),
              page: int = Query(1, ge=1, le=10000), page_size: int = Query(50, ge=1, le=200)):
    qry = ctx.db.query(AuditLog)
    if action:
        qry = qry.filter(AuditLog.action == action)
    if result:
        qry = qry.filter(AuditLog.result == result)
    if severity:
        qry = qry.filter(AuditLog.severity == severity)
    cats = {
        "LOGIN": ["LOGIN_SUCCESS", "LOGIN_FAILED", "LOGIN_BLOCKED_LOCKED", "LOGIN_RATE_LIMITED", "LOGOUT", "DEMO_MODE_ENTERED", "ACCOUNT_LOCKED"],
        "UPLOAD": ["FILE_UPLOAD", "FILE_REJECTED"],
        "MISSION": ["MISSION_CREATED", "MISSION_UPDATED", "MISSION_DELETED", "PIPELINE_STARTED", "ZONES_UPDATED"],
        "REPORT": ["REPORT_GENERATED", "REPORT_DOWNLOADED"],
        "CONFIG": ["SETTINGS_CHANGED", "USER_CREATED", "USER_UPDATED", "MODEL_REGISTERED", "PASSWORD_CHANGE"],
        "REVIEW": ["TRACK_REVIEWED", "EVENT_REVIEWED"],
        "ACCESS": ["PERMISSION_DENIED", "AUTH_SESSION_REJECTED", "CSRF_REJECTED", "SESSION_REVOKED", "API_ERROR"],
        "LIVE": ["LIVE_STREAM_STARTED", "LIVE_STREAM_STOPPED", "LIVE_RECORDING"],
    }
    if category and category in cats:
        qry = qry.filter(AuditLog.action.in_(cats[category]))
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(AuditLog.username.ilike(like), AuditLog.resource.ilike(like), AuditLog.event_id.ilike(like)))
    total = qry.count()
    rows = qry.order_by(AuditLog.timestamp.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [_audit_out(a) for a in rows],
            "categories": list(cats)}


@router.get("/sessions")
def sessions(ctx: Ctx = Depends(require("audit:read"))):
    rows = _active_sessions_q(ctx.db).order_by(Session.last_seen_at.desc()).all()
    return [{"id": s.id, "ref": s.ref, "user": s.user.username, "role": s.user.role, "ip": s.ip,
             "user_agent": (s.user_agent or "")[:60], "created_at": s.created_at.isoformat(),
             "last_seen_at": s.last_seen_at.isoformat(), "expires_at": s.expires_at.isoformat(),
             "current": s.id == ctx.session.id, "demo": s.user.is_demo} for s in rows]


@router.delete("/sessions/{session_id}")
def revoke(session_id: int, ctx: Ctx = Depends(require("sessions:revoke_any"))):
    s = ctx.db.get(Session, session_id)
    if s is None:
        raise APIError(404, "NOT_FOUND", "Session not found.")
    s.revoked_at = now_utc()
    ctx.db.commit()
    ctx.audit("SESSION_REVOKED", resource=f"session:{s.ref}", severity=Severity.WARNING.value,
              details={"target_user": s.user.username})
    return {"ok": True}


@router.get("/system-events")
def system_events(ctx: Ctx = Depends(require("audit:read")), limit: int = Query(100, ge=1, le=500)):
    rows = ctx.db.query(SystemEvent).order_by(SystemEvent.timestamp.desc()).limit(limit).all()
    return [{"id": r.id, "timestamp": r.timestamp.isoformat(), "category": r.category, "severity": r.severity,
             "message": r.message, "mission_id": r.mission_id} for r in rows]
