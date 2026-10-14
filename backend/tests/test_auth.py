"""Authentication, sessions, CSRF and lockout."""
from datetime import timedelta

from app.database import db_session
from app.models import AuditLog, Session, User
from app.security import has_permission, hash_password, now_utc, password_ok, sanitize_display_name, verify_password

from .conftest import PASSWORDS, login


def test_password_hashing_argon2():
    h = hash_password("CorrectHorse9")
    assert h.startswith("$argon2id$")
    assert "CorrectHorse9" not in h
    assert verify_password("CorrectHorse9", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", None)


def test_password_policy():
    assert password_ok("abcdefgh12")
    assert not password_ok("short1")
    assert not password_ok("onlyletterslong")


def test_login_success_sets_httponly_cookie(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORDS["admin"]})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["role"] == "ADMIN"
    assert "csrf_token" in body
    set_cookie = r.headers.get_list("set-cookie")
    sess = [c for c in set_cookie if c.startswith("aegis_session=")][0]
    assert "HttpOnly" in sess and "SameSite=strict" in sess
    client.cookies.clear()


def test_login_failure_generic_message(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    r2 = client.post("/api/auth/login", json={"username": "ghost-user", "password": "nope"})
    assert r2.json()["error"]["message"] == r.json()["error"]["message"]  # no user enumeration
    with db_session() as db:
        assert db.query(AuditLog).filter(AuditLog.action == "LOGIN_FAILED").count() >= 2


def test_failed_login_tracking_and_lockout(client):
    with db_session() as db:
        db.add(User(username="locktest", role="VIEWER", password_hash=hash_password("LockTestPass1")))
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "locktest", "password": "bad"}).status_code == 401
    r = client.post("/api/auth/login", json={"username": "locktest", "password": "LockTestPass1"})
    assert r.status_code == 423
    assert r.json()["error"]["code"] == "ACCOUNT_LOCKED"
    with db_session() as db:
        u = db.query(User).filter(User.username == "locktest").first()
        assert u.failed_attempts >= 5 and u.locked_until is not None


def test_rate_limit_login(client):
    codes = [client.post("/api/auth/login", json={"username": f"x{i}", "password": "y"}).status_code for i in range(12)]
    assert 429 in codes


def test_me_requires_auth(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "NOT_AUTHENTICATED"


def test_invalid_session_token(client):
    r = client.get("/api/missions", headers={"Authorization": "Bearer forged-token-value"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_SESSION"


def test_session_expiration(client):
    h = login(client, "viewer1")
    token = h["Authorization"].split()[1]
    import hashlib
    with db_session() as db:
        s = db.query(Session).filter(Session.token_hash == hashlib.sha256(token.encode()).hexdigest()).first()
        s.expires_at = now_utc() - timedelta(minutes=1)
    r = client.get("/api/auth/me", headers=h)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "SESSION_EXPIRED"


def test_logout_revokes_session(client):
    h = login(client, "viewer1")
    assert client.get("/api/auth/me", headers=h).status_code == 200
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_csrf_required_for_cookie_auth(client):
    r = client.post("/api/auth/login", json={"username": "analyst1", "password": PASSWORDS["analyst1"]})
    csrf = r.json()["csrf_token"]
    bad = client.post("/api/missions", json={"name": "No CSRF", "latitude": 1, "longitude": 1})
    assert bad.status_code == 403 and bad.json()["error"]["code"] == "CSRF_FAILED"
    good = client.post("/api/missions", json={"name": "With CSRF", "latitude": 1, "longitude": 1}, headers={"X-CSRF-Token": csrf})
    assert good.status_code == 201
    client.cookies.clear()


def test_rbac_matrix():
    admin, viewer, analyst, operator = (User(role=r) for r in ("ADMIN", "VIEWER", "ANALYST", "OPERATOR"))
    for u in (admin, viewer, analyst, operator):
        u.is_demo = False
    assert has_permission(admin, "users:manage")
    assert not has_permission(viewer, "mission:write")
    assert has_permission(operator, "media:upload") and not has_permission(operator, "review:write")
    assert has_permission(analyst, "review:write") and not has_permission(analyst, "audit:read")
    demo_admin = User(role="ADMIN", is_demo=True)
    assert not has_permission(demo_admin, "users:manage")


def test_filename_sanitisation():
    assert sanitize_display_name("../../etc/passwd.jpg") == "passwd.jpg"
    assert sanitize_display_name("..\\..\\win.ini") == "win.ini"
    assert "/" not in sanitize_display_name("a/b/c<script>.png")
    assert sanitize_display_name("\x00") == "unnamed"


def test_demo_mode_entry(client):
    r = client.post("/api/auth/demo")
    assert r.status_code == 200
    assert r.json()["user"]["is_demo"] is True
    client.cookies.clear()
