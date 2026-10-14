"""Authentication: login, logout, session info, demo entry, password change."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session as DBSession

from .. import audit
from ..config import get_settings
from ..database import get_db
from ..deps import Ctx, client_ip, get_ctx
from ..errors import APIError
from ..models import Severity, User
from ..schemas import LoginIn, PasswordChangeIn, user_out
from ..security import (
    CSRF_COOKIE,
    PASSWORD_POLICY,
    SESSION_COOKIE,
    create_session,
    hash_password,
    is_locked,
    now_utc,
    password_ok,
    permissions_for,
    rate_limiter,
    revoke_session,
    verify_password,
)
from ..services import demo as demo_svc

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_cookies(resp: Response, token: str, csrf: str) -> None:
    s = get_settings()
    max_age = s.session_ttl_minutes * 60
    resp.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=s.cookie_secure,
                    samesite="strict", path="/")
    resp.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, httponly=False, secure=s.cookie_secure,
                    samesite="strict", path="/")


def _session_payload(user: User, sess, csrf: str | None = None) -> dict:
    s = get_settings()
    out = {"user": user_out(user), "permissions": permissions_for(user),
           "session": {"ref": sess.ref, "expires_at": sess.expires_at.isoformat(),
                       "idle_timeout_minutes": s.session_idle_minutes}}
    if csrf:
        out["csrf_token"] = csrf
    return out


@router.get("/config")
def public_config():
    """Non-sensitive configuration for the login screen."""
    s = get_settings()
    return {"app_name": s.app_name, "version": s.version, "demo_enabled": s.demo_mode_enabled,
            "online_tiles_enabled": s.online_tiles_enabled, "local_processing": True, "cloud_upload": False,
            "password_policy": PASSWORD_POLICY}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: DBSession = Depends(get_db)):
    ip = client_ip(request)
    s = get_settings()
    if not rate_limiter.allow(f"login:{ip}", 10, 60) or not rate_limiter.allow(f"login-u:{body.username.lower()}", 8, 60):
        audit.record(db, "LOGIN_RATE_LIMITED", resource=body.username[:64], result="DENIED",
                     severity=Severity.HIGH.value, ip=ip, username=body.username[:64])
        raise APIError(429, "RATE_LIMITED", "Too many login attempts. Wait a minute and try again.")
    user = db.query(User).filter(User.username == body.username).first()
    if user is not None and user.is_demo:
        user = None  # demo account cannot be used with a password
    if user is not None and is_locked(user):
        verify_password(body.password, None)
        audit.record(db, "LOGIN_BLOCKED_LOCKED", resource=user.username, result="DENIED", severity=Severity.HIGH.value,
                     ip=ip, user=user)
        raise APIError(423, "ACCOUNT_LOCKED", "Account temporarily locked after repeated failed sign-ins.")
    ok = verify_password(body.password, user.password_hash if user else None)
    if not ok or user is None or not user.is_active:
        sev = Severity.WARNING.value
        if user is not None:
            user.failed_attempts += 1
            if user.failed_attempts >= s.max_failed_logins:
                user.locked_until = now_utc() + timedelta(minutes=s.lockout_minutes)
                sev = Severity.HIGH.value
                audit.record(db, "ACCOUNT_LOCKED", resource=user.username, result="DENIED", severity=Severity.HIGH.value,
                             ip=ip, user=user, details={"failed_attempts": user.failed_attempts}, commit=False)
            db.commit()
        audit.record(db, "LOGIN_FAILED", resource=body.username[:64], result="FAILURE", severity=sev, ip=ip,
                     username=body.username[:64], details={"reason": "invalid_credentials"})
        # generic message: never reveal whether the username exists
        raise APIError(401, "INVALID_CREDENTIALS", "Invalid username or password.")
    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = now_utc()
    sess, token, csrf = create_session(db, user, ip, request.headers.get("user-agent", ""))
    db.commit()
    audit.record(db, "LOGIN_SUCCESS", resource=user.username, user=user, session_ref=sess.ref, ip=ip)
    _set_cookies(response, token, csrf)
    return _session_payload(user, sess, csrf)


@router.post("/demo")
def enter_demo(request: Request, response: Response, db: DBSession = Depends(get_db)):
    s = get_settings()
    if not s.demo_mode_enabled:
        raise APIError(403, "DEMO_DISABLED", "Demo mode is disabled on this deployment.")
    ip = client_ip(request)
    if not rate_limiter.allow(f"demo:{ip}", 10, 60):
        raise APIError(429, "RATE_LIMITED", "Too many requests.")
    demo_svc.ensure_demo_user()
    user = db.query(User).filter(User.username == demo_svc.DEMO_USER).first()
    user.last_login_at = now_utc()
    sess, token, csrf = create_session(db, user, ip, request.headers.get("user-agent", ""))
    db.commit()
    audit.record(db, "DEMO_MODE_ENTERED", resource=user.username, user=user, session_ref=sess.ref, ip=ip)
    demo_svc.ensure_demo_environment(background=True)
    _set_cookies(response, token, csrf)
    return _session_payload(user, sess, csrf) | {"demo": demo_svc.seed_status()}


@router.get("/me")
def me(ctx: Ctx = Depends(get_ctx)):
    out = _session_payload(ctx.user, ctx.session)
    if ctx.user.is_demo:
        out["demo"] = demo_svc.seed_status()
    return out


@router.post("/logout")
def logout(response: Response, ctx: Ctx = Depends(get_ctx)):
    revoke_session(ctx.db, ctx.session)
    ctx.audit("LOGOUT", resource=ctx.user.username)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.post("/change-password")
def change_password(body: PasswordChangeIn, ctx: Ctx = Depends(get_ctx)):
    if ctx.user.is_demo:
        raise APIError(403, "PERMISSION_DENIED", "Demo account password cannot be changed.")
    if not verify_password(body.current_password, ctx.user.password_hash):
        ctx.audit("PASSWORD_CHANGE", resource=ctx.user.username, result="FAILURE", severity=Severity.WARNING.value)
        raise APIError(400, "INVALID_CREDENTIALS", "Current password is incorrect.")
    if not password_ok(body.new_password):
        raise APIError(400, "WEAK_PASSWORD", PASSWORD_POLICY)
    ctx.user.password_hash = hash_password(body.new_password)
    ctx.db.commit()
    ctx.audit("PASSWORD_CHANGE", resource=ctx.user.username)
    return {"ok": True}
