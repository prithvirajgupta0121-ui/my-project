"""User administration (ADMIN only)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import Ctx, require
from ..errors import APIError
from ..models import Severity, User
from ..schemas import UserCreateIn, UserUpdateIn, user_out
from ..security import PASSWORD_POLICY, hash_password, password_ok

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("")
def list_users(ctx: Ctx = Depends(require("users:manage"))):
    return [user_out(u) for u in ctx.db.query(User).order_by(User.username).all()]


@router.post("", status_code=201)
def create_user(body: UserCreateIn, ctx: Ctx = Depends(require("users:manage"))):
    if ctx.db.query(User).filter(User.username == body.username).first():
        raise APIError(409, "CONFLICT", "Username already exists.")
    if not password_ok(body.password):
        raise APIError(400, "WEAK_PASSWORD", PASSWORD_POLICY)
    u = User(username=body.username, full_name=body.full_name, role=body.role.value,
             password_hash=hash_password(body.password))
    ctx.db.add(u)
    ctx.db.commit()
    ctx.audit("USER_CREATED", resource=f"user:{u.username}", severity=Severity.WARNING.value,
              details={"role": u.role})
    return user_out(u)


@router.patch("/{user_id}")
def update_user(user_id: int, body: UserUpdateIn, ctx: Ctx = Depends(require("users:manage"))):
    u = ctx.db.get(User, user_id)
    if u is None or u.is_demo:
        raise APIError(404, "NOT_FOUND", "User not found.")
    changes = {}
    if body.role is not None and body.role.value != u.role:
        if u.id == ctx.user.id:
            raise APIError(400, "INVALID_OPERATION", "You cannot change your own role.")
        changes["role"] = [u.role, body.role.value]
        u.role = body.role.value
    if body.full_name is not None:
        u.full_name = body.full_name
    if body.is_active is not None and body.is_active != u.is_active:
        if u.id == ctx.user.id:
            raise APIError(400, "INVALID_OPERATION", "You cannot deactivate your own account.")
        changes["is_active"] = body.is_active
        u.is_active = body.is_active
        if not body.is_active:
            for s in u.sessions:
                s.revoked_at = s.revoked_at or s.last_seen_at
    if body.unlock:
        u.failed_attempts = 0
        u.locked_until = None
        changes["unlocked"] = True
    if body.new_password:
        if not password_ok(body.new_password):
            raise APIError(400, "WEAK_PASSWORD", PASSWORD_POLICY)
        u.password_hash = hash_password(body.new_password)
        changes["password_reset"] = True
    ctx.db.commit()
    ctx.audit("USER_UPDATED", resource=f"user:{u.username}", severity=Severity.WARNING.value, details=changes)
    return user_out(u)
