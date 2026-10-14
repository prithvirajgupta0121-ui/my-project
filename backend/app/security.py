"""Authentication, session management, RBAC, CSRF, rate limiting and input sanitisation."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy.orm import Session as DBSession

from .config import get_settings
from .models import Role, Session, User

# --------------------------------------------------------------------------
# Password hashing (Argon2id via argon2-cffi)
# --------------------------------------------------------------------------
_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
# A dummy hash used to equalise timing when a username does not exist.
_DUMMY_HASH = _ph.hash("aegis-timing-equaliser")

PASSWORD_POLICY = "Minimum 10 characters, including letters and digits."


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _ph.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_ok(pw: str) -> bool:
    return len(pw) >= 10 and re.search(r"[A-Za-z]", pw) is not None and re.search(r"\d", pw) is not None


# --------------------------------------------------------------------------
# RBAC
# --------------------------------------------------------------------------
PERMISSIONS: dict[str, set[str]] = {
    Role.VIEWER.value: {
        "mission:read", "report:read", "analytics:read", "live:view", "system:read",
    },
    Role.OPERATOR.value: {
        "mission:read", "mission:write", "media:upload", "pipeline:run",
        "report:read", "analytics:read", "live:view", "live:operate", "system:read",
    },
    Role.ANALYST.value: {
        "mission:read", "mission:write", "media:upload", "pipeline:run", "review:write",
        "zone:write", "report:read", "report:generate", "analytics:read",
        "live:view", "live:operate", "system:read", "mission:archive",
    },
    Role.ADMIN.value: {"*"},
}
# Permissions a demo account is never granted, even with the ADMIN role.
DEMO_DENIED = {"users:manage", "settings:write", "sessions:revoke_any", "mission:delete"}


def has_permission(user: User, perm: str) -> bool:
    if user.is_demo and perm in DEMO_DENIED:
        return False
    perms = PERMISSIONS.get(user.role, set())
    return "*" in perms or perm in perms


def permissions_for(user: User) -> list[str]:
    perms = PERMISSIONS.get(user.role, set())
    if "*" in perms:
        allp = set().union(*[p for k, p in PERMISSIONS.items() if k != Role.ADMIN.value])
        allp |= {"audit:read", "users:manage", "settings:write", "sessions:revoke_any", "mission:delete"}
        perms = allp
    if user.is_demo:
        perms = set(perms) - DEMO_DENIED
    return sorted(perms)


# --------------------------------------------------------------------------
# Sessions — opaque random tokens; only SHA-256 digests are stored.
# --------------------------------------------------------------------------
SESSION_COOKIE = "aegis_session"
CSRF_COOKIE = "aegis_csrf"
CSRF_HEADER = "x-csrf-token"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def create_session(db: DBSession, user: User, ip: str, user_agent: str) -> tuple[Session, str, str]:
    s = get_settings()
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    sess = Session(
        ref="S-" + secrets.token_hex(3).upper(),
        token_hash=_digest(token),
        csrf_hash=_digest(csrf),
        user_id=user.id,
        expires_at=now_utc() + timedelta(minutes=s.session_ttl_minutes),
        ip=ip[:64],
        user_agent=(user_agent or "")[:256],
    )
    db.add(sess)
    db.flush()
    return sess, token, csrf


class SessionError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message


def resolve_session(db: DBSession, token: str | None) -> Session:
    if not token:
        raise SessionError("NOT_AUTHENTICATED", "Authentication required.")
    sess = db.query(Session).filter(Session.token_hash == _digest(token)).first()
    if sess is None or sess.revoked_at is not None:
        raise SessionError("INVALID_SESSION", "Session is invalid or has been revoked.")
    s = get_settings()
    now = now_utc()
    if _aware(sess.expires_at) < now:
        raise SessionError("SESSION_EXPIRED", "Session expired. Please sign in again.")
    if _aware(sess.last_seen_at) + timedelta(minutes=s.session_idle_minutes) < now:
        sess.revoked_at = now
        db.commit()
        raise SessionError("SESSION_EXPIRED", "Session expired due to inactivity.")
    if not sess.user.is_active:
        raise SessionError("ACCOUNT_DISABLED", "Account disabled.")
    # throttle last-seen writes
    if (now - _aware(sess.last_seen_at)).total_seconds() > 30:
        sess.last_seen_at = now
        db.commit()
    return sess


def verify_csrf(sess: Session, header_value: str | None) -> bool:
    if not header_value:
        return False
    return hmac.compare_digest(sess.csrf_hash, _digest(header_value))


def revoke_session(db: DBSession, sess: Session) -> None:
    sess.revoked_at = now_utc()
    db.commit()


def is_locked(user: User) -> bool:
    lu = _aware(user.locked_until)
    return lu is not None and lu > now_utc()


# --------------------------------------------------------------------------
# Rate limiting — in-process sliding window (swap for Redis in multi-node).
# --------------------------------------------------------------------------
class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_s: float) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = RateLimiter()


# --------------------------------------------------------------------------
# Input sanitisation
# --------------------------------------------------------------------------
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]")


def sanitize_display_name(name: str, max_len: int = 120) -> str:
    """Sanitise a client-provided filename for DISPLAY ONLY.

    Stored files always use server-generated UUID names; the client name is
    never used to build a filesystem path.
    """
    name = name.replace("\\", "/").split("/")[-1]
    name = name.replace("\x00", "")
    name = _SAFE_CHARS.sub("_", name).strip(" .")
    name = re.sub(r"\.{2,}", ".", name)
    return (name or "unnamed")[:max_len]
