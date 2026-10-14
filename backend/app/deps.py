"""Request dependencies: authentication context and permission checks (zero-trust: every request re-verified)."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.orm import Session as DBSession

from . import audit
from .database import get_db
from .errors import APIError
from .models import Mission, Session, Severity, User
from .security import (
    CSRF_HEADER,
    SESSION_COOKIE,
    SessionError,
    has_permission,
    resolve_session,
    verify_csrf,
)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@dataclass
class Ctx:
    user: User
    session: Session
    ip: str
    db: DBSession

    def can(self, perm: str) -> bool:
        return has_permission(self.user, perm)

    def audit(self, action: str, **kw) -> None:
        audit.record(self.db, action, user=self.user, session_ref=self.session.ref, ip=self.ip, **kw)


def _extract_token(request: Request) -> tuple[str | None, bool]:
    """Return (token, via_cookie)."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), False
    return request.cookies.get(SESSION_COOKIE), True


def get_ctx(request: Request, db: DBSession = Depends(get_db)) -> Ctx:
    token, via_cookie = _extract_token(request)
    ip = client_ip(request)
    try:
        sess = resolve_session(db, token)
    except SessionError as e:
        if token:  # only log presented-but-bad credentials, not anonymous probes
            audit.record(db, "AUTH_SESSION_REJECTED", resource=request.url.path, result="DENIED",
                         severity=Severity.WARNING.value, ip=ip, details={"reason": e.code})
        raise APIError(401, e.code, e.message)
    # Cookie-authenticated state-changing requests must carry the CSRF token.
    if via_cookie and request.method not in SAFE_METHODS:
        if not verify_csrf(sess, request.headers.get(CSRF_HEADER)):
            audit.record(db, "CSRF_REJECTED", resource=request.url.path, result="DENIED",
                         severity=Severity.HIGH.value, user=sess.user, session_ref=sess.ref, ip=ip)
            raise APIError(403, "CSRF_FAILED", "Missing or invalid CSRF token.")
    request.state.user = sess.user.username
    request.state.session_ref = sess.ref
    return Ctx(user=sess.user, session=sess, ip=ip, db=db)


def require(perm: str):
    def _dep(ctx: Ctx = Depends(get_ctx), request: Request = None) -> Ctx:  # type: ignore[assignment]
        if not ctx.can(perm):
            ctx.audit("PERMISSION_DENIED", resource=f"{request.method} {request.url.path}" if request else perm,
                      result="DENIED", severity=Severity.WARNING.value, details={"permission": perm})
            raise APIError(403, "PERMISSION_DENIED",
                           f"Your role ({ctx.user.role}{', demo' if ctx.user.is_demo else ''}) lacks permission '{perm}'.")
        return ctx
    return _dep


def load_mission(db: DBSession, mission_id: int, ctx: Ctx) -> Mission:
    """Resolve a mission by server-side ID and check the caller may see it."""
    m = db.get(Mission, mission_id)
    if m is None:
        raise APIError(404, "NOT_FOUND", "Mission not found.")
    # Demo accounts are sandboxed to demo missions; real accounts see all real + demo missions.
    if ctx.user.is_demo and not m.is_demo:
        raise APIError(404, "NOT_FOUND", "Mission not found.")
    return m
